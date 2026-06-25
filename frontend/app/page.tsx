"use client";

import { useCallback, useRef, useState } from "react";
import CodeEditor from "./components/CodeEditor";
import TraceAnimator from "./components/TraceAnimator";
import TerminalLoader from "./components/TerminalLoader";
import ExplanationPanel from "./components/ExplanationPanel";
import SocraticPanel from "./components/SocraticPanel";
import PipelineStrip, {
  INITIAL_PIPELINE,
  type PipelineState,
} from "./components/PipelineStrip";
import {
  runGrading,
  type Violation,
  type AdversarialResult,
  type Socratic,
} from "./lib/grading-stream";

type PatternKey = "sliding_window" | "two_pointers" | "binary_search";

interface PatternMeta {
  key: PatternKey;
  label: string;
  hint: string;
  template: string;
}

// Layer 1 (UI guide): boilerplate with arr / left / right pre-filled so the AST
// layer can resolve pointer names. This is the scaffold, not the constraint.
const PATTERNS: PatternMeta[] = [
  {
    key: "sliding_window",
    label: "Sliding Window",
    hint: "Use when: finding a subarray/substring satisfying a condition",
    template: `def solve(arr, k):
    left = 0
    window_sum = 0
    best = 0
    for right in range(len(arr)):
        window_sum += arr[right]
        if right - left + 1 > k:
            window_sum -= arr[left]
            left += 1
        best = max(best, window_sum)
    return best
`,
  },
  {
    key: "two_pointers",
    label: "Two Pointers",
    hint: "Use when: searching sorted arrays, removing duplicates in-place",
    template: `def solve(arr, target):
    left = 0
    right = len(arr) - 1
    while left < right:
        total = arr[left] + arr[right]
        if total == target:
            return [left, right]
        elif total < target:
            left += 1
        else:
            right -= 1
    return []
`,
  },
  {
    key: "binary_search",
    label: "Binary Search",
    hint: "Use when: searching a sorted array in O(log N)",
    template: `def solve(arr, target):
    lo = 0
    hi = len(arr) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1
`,
  },
];

const TEMPLATE: Record<PatternKey, string> = {
  sliding_window: PATTERNS[0].template,
  two_pointers: PATTERNS[1].template,
  binary_search: PATTERNS[2].template,
};

// Banner copy + colour per backend code (plus "clean" success and a "network"
// fallback). Distinct copy/colour for every case — never a single generic string.
const BANNER: Record<string, { text: string; cls: string }> = {
  clean: {
    text: "No anti-patterns detected. (Verification is limited to contract structural checks.)",
    cls: "border-green-500/50 bg-green-500/10 text-green-300",
  },
  no_iteration: {
    text: "No loop found — this code doesn't implement the selected pattern. There's no iteration structure to verify.",
    cls: "border-red-500/50 bg-red-500/10 text-red-300",
  },
  syntax_error: {
    text: "Syntax error in submission. Check your Python.",
    cls: "border-yellow-500/50 bg-yellow-500/10 text-yellow-300",
  },
  invalid_pattern: {
    text: "Unknown pattern selected.",
    cls: "border-yellow-500/50 bg-yellow-500/10 text-yellow-300",
  },
  llm_schema: {
    text: "AI response malformed after retry. Try again.",
    cls: "border-orange-500/50 bg-orange-500/10 text-orange-300",
  },
  llm_unavailable: {
    text: "AI service unreachable. AST violations still shown above.",
    cls: "border-orange-500/50 bg-orange-500/10 text-orange-300",
  },
  pipeline_error: {
    text: "Unexpected error. See console.",
    cls: "border-red-500/50 bg-red-500/10 text-red-300",
  },
};

// On an error, mark the stage that was in flight as errored.
function markActiveAsError(p: PipelineState): PipelineState {
  const order: (keyof PipelineState)[] = ["ast", "adv", "sandbox", "explain"];
  const next = { ...p };
  const active = order.find((k) => next[k] === "active");
  if (active) {
    next[active] = "error";
    return next;
  }
  const firstIdle = order.find((k) => next[k] === "idle");
  if (firstIdle) next[firstIdle] = "error";
  return next;
}

export default function Home() {
  const [pattern, setPattern] = useState<PatternKey>("sliding_window");
  const [source, setSource] = useState<string>(TEMPLATE.sliding_window);

  const [violations, setViolations] = useState<Violation[]>([]);
  const [adversarial, setAdversarial] = useState<AdversarialResult | null>(null);
  // Bumped each time a trace arrives, so TraceAnimator + SocraticPanel remount.
  const [traceVersion, setTraceVersion] = useState(0);
  const [explanation, setExplanation] = useState<string>("");
  const [socratic, setSocratic] = useState<Socratic | null>(null);
  const [bannerCode, setBannerCode] = useState<string | null>(null);
  const [pipeline, setPipeline] = useState<PipelineState>(INITIAL_PIPELINE);
  const [loading, setLoading] = useState(false);

  // The editor line currently executing in the animation (bidirectional sync).
  const [activeLine, setActiveLine] = useState<number | null>(null);
  // Live terminal-style pipeline log shown while the LLM + sandbox run.
  const [logs, setLogs] = useState<string[]>([]);
  // Socratic stays gated until the animation completes (or the user reveals it).
  const [socraticUnlocked, setSocraticUnlocked] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const startRef = useRef<number>(0);
  const explainLoggedRef = useRef(false);

  const pushLog = useCallback((line: string) => {
    const ms = Math.round(performance.now() - startRef.current);
    setLogs((l) => [...l, `[${ms}ms] ${line}`]);
  }, []);

  const handleAnimationComplete = useCallback(() => setSocraticUnlocked(true), []);

  function selectPattern(key: PatternKey) {
    if (key === pattern) return;
    // Only replace the editor if it's still the default boilerplate, otherwise
    // confirm — switching patterns must not silently destroy edited code.
    const atDefault = source === TEMPLATE[pattern];
    if (!atDefault) {
      const label = PATTERNS.find((p) => p.key === key)!.label;
      const ok = window.confirm(
        `Switch to ${label}? Your current code will be replaced.`,
      );
      if (!ok) return;
    }
    setPattern(key);
    setSource(TEMPLATE[key]); // setValue() into Monaco via controlled prop
    // Clear stale results so decorations/animation don't linger.
    setViolations([]);
    setAdversarial(null);
    setExplanation("");
    setSocratic(null);
    setBannerCode(null);
    setPipeline(INITIAL_PIPELINE);
    setActiveLine(null);
    setLogs([]);
    setSocraticUnlocked(false);
  }

  function handleSubmit() {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setViolations([]);
    setAdversarial(null);
    setExplanation("");
    setSocratic(null);
    setBannerCode(null);
    setPipeline({ ...INITIAL_PIPELINE, ast: "active" });
    setLoading(true);
    setActiveLine(null);
    setSocraticUnlocked(false);
    startRef.current = performance.now();
    explainLoggedRef.current = false;
    setLogs([]);
    pushLog(`POST /grade — pattern: ${pattern}`);

    runGrading(
      { source, pattern },
      {
        onViolations: (v) => {
          setViolations(v);
          pushLog(`AST contract verified — ${v.length} violation${v.length === 1 ? "" : "s"}`);
          if (v.length > 0) pushLog("Generating adversarial input…");
          setPipeline((p) => ({
            ...p,
            ast: "done",
            adv: v.length > 0 ? "active" : p.adv,
          }));
        },
        onAdversarialTrace: (result) => {
          setAdversarial(result);
          setTraceVersion((v) => v + 1);
          if (result.error && result.error !== "infinite_loop") {
            pushLog(`Sandbox halted — ${result.error}`);
          } else {
            pushLog(
              `Sandbox executed — ${result.trace.length} frame${result.trace.length === 1 ? "" : "s"} captured`,
            );
          }
          setPipeline((p) => ({ ...p, adv: "done", sandbox: "done" }));
        },
        onToken: (text) => {
          if (!explainLoggedRef.current) {
            explainLoggedRef.current = true;
            pushLog("Streaming explanation…");
          }
          setExplanation((prev) => prev + text);
          setPipeline((p) =>
            p.explain === "idle" ? { ...p, explain: "active" } : p,
          );
        },
        onSocratic: setSocratic,
        onError: (code, message) => {
          setBannerCode(code);
          pushLog(`✗ ${code}`);
          if (code === "pipeline_error" || code === "network") {
            console.error("[AlgoLens]", code, message);
          }
          setPipeline(markActiveAsError);
          setLoading(false);
        },
        onDone: (info) => {
          setLoading(false);
          // Early-exit paths (clean / loopless) never run adv→sandbox→explain;
          // snap the strip back to "AST done, rest idle" so nothing keeps pulsing.
          if (info.status === "clean" || info.status === "no_iteration") {
            setBannerCode(info.status);
            setPipeline({ ...INITIAL_PIPELINE, ast: "done" });
            pushLog(
              info.status === "clean"
                ? "Done — no anti-patterns"
                : "Done — no iteration structure",
            );
            return;
          }
          pushLog("Done.");
          setPipeline((p) =>
            p.explain === "active" ? { ...p, explain: "done" } : p,
          );
        },
      },
      controller.signal,
    );
  }

  const banner = bannerCode ? BANNER[bannerCode] ?? BANNER.pipeline_error : null;

  // Show the terminal log while the LLM + sandbox run (no trace yet); swap to the animator the moment the adversarial trace arrives.
  const showTerminal = loading && !adversarial;

  return (
    <main
      className="flex h-screen flex-col overflow-hidden px-6 py-4 text-neutral-100"
      style={{ backgroundColor: "#050505" }}
    >
      <div className="mx-auto flex min-h-0 w-full max-w-7xl flex-1 flex-col">
        {/* Header */}
        <header className="mb-3 shrink-0">
          <h1
            className="font-display text-xl tracking-tight"
            style={{ color: "#C97832" }}
          >
            AlgoLens
          </h1>
          <p className="mt-1.5 text-sm text-neutral-500">
            Not a linter. A pedagogue.
          </p>
        </header>

        {/* Pattern selector with inline use-case hints */}
        <div className="mb-3 flex shrink-0 flex-wrap gap-3">
          {PATTERNS.map((p) => {
            const active = p.key === pattern;
            return (
              <button
                key={p.key}
                onClick={() => selectPattern(p.key)}
                className={`min-w-60 flex-1 rounded-lg border px-4 py-2.5 text-left transition-colors ${
                  active
                    ? "border-neutral-500 bg-neutral-900"
                    : "border-neutral-800 hover:border-neutral-700"
                }`}
              >
                <div
                  className="text-sm font-semibold"
                  style={{ color: active ? "#C97832" : undefined }}
                >
                  {p.label}
                </div>
                <div className="mt-0.5 text-xs text-neutral-500">{p.hint}</div>
              </button>
            );
          })}
        </div>

        {/* Agent pipeline visibility */}
        <div className="shrink-0">
          <PipelineStrip pipeline={pipeline} />
        </div>

        {/* Editor + results share one viewport — no scrolling to see cause/effect. */}
        <div className="grid min-h-0 flex-1 gap-5 lg:grid-cols-2">
          {/* Left: editor + submit */}
          <div className="flex min-h-0 flex-col gap-3">
            <div className="min-h-0 flex-1">
              <CodeEditor
                value={source}
                onChange={setSource}
                violations={violations}
                activeLine={activeLine}
              />
            </div>
            <button
              onClick={handleSubmit}
              disabled={loading}
              className="shrink-0 rounded-md px-4 py-2.5 text-sm font-semibold text-black disabled:opacity-50"
              style={{ backgroundColor: "#C97832" }}
            >
              {loading ? "Grading…" : "Grade submission"}
            </button>
          </div>

          {/* Right: results — scrolls internally so the page never grows. */}
          <div className="flex min-h-0 flex-col gap-4 overflow-y-auto pr-1">
            {banner && (
              <div
                className={`shrink-0 rounded-lg border px-4 py-3 text-sm ${banner.cls}`}
              >
                {banner.text}
              </div>
            )}

            <div className="h-80 shrink-0">
              {showTerminal ? (
                <TerminalLoader logs={logs} />
              ) : (
                <TraceAnimator
                  key={traceVersion}
                  trace={adversarial?.trace ?? []}
                  error={adversarial?.error ?? null}
                  onActiveLine={setActiveLine}
                  onComplete={handleAnimationComplete}
                />
              )}
            </div>

            <ExplanationPanel text={explanation} streaming={loading} />
            <SocraticPanel
              key={`soc-${traceVersion}`}
              socratic={socratic}
              locked={!socraticUnlocked}
              onReveal={() => setSocraticUnlocked(true)}
            />
          </div>
        </div>
      </div>
    </main>
  );
}
