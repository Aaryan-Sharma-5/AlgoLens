// Typed POST-SSE client for the FastAPI /grade endpoint.
// Native EventSource is GET-only, so we use @microsoft/fetch-event-source, which supports POST + JSON body and exposes the named SSE `event:` field on each message. Event sequence: violations → adversarial_trace → explanation* → socratic → done plus a backend `error` event that must be surfaced, not crash the stream.

import { fetchEventSource } from "@microsoft/fetch-event-source";

// In production (Vercel) the browser hits the Render backend directly so the SSE stream isn't buffered by a proxy — set NEXT_PUBLIC_API_URL to the Render URL.  Locally it's unset, so requests go to `/api/grade` and the Next.js dev server rewrites `/api/*` to http://localhost:8000. CORS on the backend is `*`.
const API_BASE = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "");
const GRADE_URL = API_BASE ? `${API_BASE}/grade` : "/api/grade";

export interface Violation {
  type: string;
  lineno: number;
  severity: "critical" | "major" | "minor" | string;
  label: string;
}

export interface TraceFrame {
  left: number | null;
  right: number | null;
  arr_snapshot: number[] | null;
  lineno: number;
}

export interface AdversarialResult {
  error: string | null;
  trace: TraceFrame[];
}

export interface Socratic {
  q1: string;
  q2: string;
}

export interface GradePayload {
  source: string;
  pattern: string;
  array?: number[];
  target?: number;
}

// Typed error codes emitted by the backend (plus "network" for a transport-level failure surfaced client-side). The page maps each to distinct copy + colour.
export type GradeErrorCode =
  | "syntax_error"
  | "invalid_pattern"
  | "llm_schema"
  | "llm_unavailable"
  | "pipeline_error"
  | "network";

export interface DoneInfo {
  status?: string; // "clean" when no violations were found
  message?: string;
}

export interface GradingHandlers {
  onViolations: (violations: Violation[]) => void;
  onAdversarialTrace: (result: AdversarialResult) => void;
  onToken: (text: string) => void;
  onSocratic: (socratic: Socratic) => void;
  onError: (code: GradeErrorCode, message: string) => void;
  onDone: (info: DoneInfo) => void;
}

// Sentinel thrown to stop fetch-event-source's automatic retry loop.
class FatalStreamError extends Error {}

export async function runGrading(
  payload: GradePayload,
  handlers: GradingHandlers,
  signal?: AbortSignal,
): Promise<void> {
  try {
    await fetchEventSource(GRADE_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
      // Keep streaming even if the tab is backgrounded during a demo.
      openWhenHidden: true,

      async onopen(response) {
        const ct = response.headers.get("content-type") || "";
        if (response.ok && ct.includes("text/event-stream")) return;
        // Non-stream response (e.g. 422 validation) — read body for the message.
        let detail = `Request failed (${response.status})`;
        try {
          const body = await response.text();
          if (body) detail = body;
        } catch {
          /* ignore */
        }
        throw new FatalStreamError(detail);
      },

      onmessage(ev) {
        let data: unknown = {};
        if (ev.data) {
          try {
            data = JSON.parse(ev.data);
          } catch {
            return; // ignore malformed frames (e.g. keep-alive comments)
          }
        }
        switch (ev.event) {
          case "violations":
            handlers.onViolations(data as Violation[]);
            break;
          case "adversarial_trace":
            handlers.onAdversarialTrace(data as AdversarialResult);
            break;
          case "explanation":
            handlers.onToken((data as { text: string }).text ?? "");
            break;
          case "socratic":
            handlers.onSocratic(data as Socratic);
            break;
          case "error": {
            const d = data as { message?: string; error?: string };
            const code = (d.error as GradeErrorCode) || "pipeline_error";
            handlers.onError(code, d.message || d.error || "Unknown error");
            break;
          }
          case "done":
            handlers.onDone((data as DoneInfo) ?? {});
            break;
        }
      },

      onerror(err) {
        // Throw to prevent the library's default infinite retry.
        if (err instanceof FatalStreamError) throw err;
        throw new FatalStreamError(
          err instanceof Error ? err.message : String(err),
        );
      },
    });
  } catch (err) {
    if ((signal as AbortSignal | undefined)?.aborted) return; // intentional cancel
    handlers.onError("network", err instanceof Error ? err.message : String(err));
  }
}
