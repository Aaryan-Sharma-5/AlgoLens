"use client";

// Array-execution visualizer. The rendered frame is a PURE FUNCTION of
// `currentIdx` (trace[currentIdx]) — no state machine. `isPlaying` and `speed`
// are playback-control flags only; the interval ref drives advancement.

import { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import type { TraceFrame } from "../lib/grading-stream";

interface TraceAnimatorProps {
  trace: TraceFrame[];
  error: string | null;
  // Reports the lineno of the frame at currentIdx (or null) so the editor can highlight the executing line in sync with the animation.
  onActiveLine?: (lineno: number | null) => void;
  // Fired once when the animation reaches its final frame — gates the Socratic reveal so reflection follows the visual failure rather than competing with it.
  onComplete?: () => void;
}

type Speed = "slow" | "normal" | "fast";
const SPEED_DELAY: Record<Speed, number> = { slow: 800, normal: 400, fast: 200 };

// Design-system pointer colors.
const LEFT = "#C97832";
const RIGHT = "#4A90E2";
const DEFAULT_CELL = "#1a1a1a";

export default function TraceAnimator({
  trace,
  error,
  onActiveLine,
  onComplete,
}: TraceAnimatorProps) {
  // The page remounts this component (via a changing `key`) whenever a new trace arrives, so these initializers re-run per trace — auto-play starts on mount with no synchronous setState-in-effect. currentIdx remains the single source of truth for the rendered frame.
  const [currentIdx, setCurrentIdx] = useState(0);
  const [isPlaying, setIsPlaying] = useState(() => trace.length > 1);
  const [speed, setSpeed] = useState<Speed>("normal");
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const completedRef = useRef(false);

  const lastIdx = Math.max(0, trace.length - 1);
  const atEnd = currentIdx >= lastIdx;

  // Sync the executing line to the editor as the frame changes.
  useEffect(() => {
    const f = trace[currentIdx];
    onActiveLine?.(f ? f.lineno : null);
  }, [currentIdx, trace, onActiveLine]);

  // Fire onComplete once when the final frame is reached (component remounts per trace via a changing key, so completedRef resets naturally each grade).
  useEffect(() => {
    if (trace.length > 0 && currentIdx >= trace.length - 1 && !completedRef.current) {
      completedRef.current = true;
      onComplete?.();
    }
  }, [currentIdx, trace.length, onComplete]);

  // Playback loop: a setInterval that increments currentIdx, cleared on stop.
  useEffect(() => {
    if (!isPlaying || trace.length === 0) return;
    intervalRef.current = setInterval(() => {
      setCurrentIdx((i) => {
        if (i >= trace.length - 1) {
          setIsPlaying(false);
          return i;
        }
        return i + 1;
      });
    }, SPEED_DELAY[speed]);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [isPlaying, speed, trace.length]);

  function togglePlay() {
    if (trace.length === 0) return;
    if (atEnd) setCurrentIdx(0); // restart from the beginning
    setIsPlaying((p) => !p);
  }
  function stepBack() {
    setIsPlaying(false);
    setCurrentIdx((i) => Math.max(0, i - 1));
  }
  function stepForward() {
    setIsPlaying(false);
    setCurrentIdx((i) => Math.min(lastIdx, i + 1));
  }

  // Empty trace: surface the sandbox error (memory_limit, etc.) or a placeholder. infinite_loop with no captured frames must still show the halt message/ not the neutral "submit code" placeholder — or the demo looks like nothing ran.
  if (trace.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 rounded-lg border border-neutral-800 p-6 text-center text-sm">
        {error === "infinite_loop" ? (
          <span className="text-red-300">
            Execution halted — pointer state never converged before timeout.
          </span>
        ) : error ? (
          <span className="text-red-400">
            Execution error: <span className="font-mono">{error}</span>
          </span>
        ) : (
          <>
            <span className="text-neutral-500">
              Submit code to watch it run on an adversarial input.
            </span>
            <div className="flex items-center gap-4 text-xs text-neutral-600">
              <span className="flex items-center gap-1.5">
                <span
                  className="inline-block h-3 w-3 rounded-sm"
                  style={{ backgroundColor: LEFT }}
                />
                left pointer
              </span>
              <span className="flex items-center gap-1.5">
                <span
                  className="inline-block h-3 w-3 rounded-sm"
                  style={{ backgroundColor: RIGHT }}
                />
                right pointer
              </span>
            </div>
          </>
        )}
      </div>
    );
  }

  const frame = trace[currentIdx];
  const cells = frame.arr_snapshot ?? [];
  const haltedInfiniteLoop = error === "infinite_loop" && atEnd && !isPlaying;

  return (
    <div className="flex h-full flex-col gap-4 rounded-lg border border-neutral-800 p-4">
      {/* Pointer readout */}
      <div className="flex flex-wrap items-center gap-4 text-xs text-neutral-400">
        <span>
          iteration <span className="font-mono text-neutral-200">{currentIdx + 1}</span> / {trace.length}
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-3 rounded-sm" style={{ backgroundColor: LEFT }} />
          left = <span className="font-mono text-neutral-200">{String(frame.left)}</span>
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-3 rounded-sm" style={{ backgroundColor: RIGHT }} />
          right = <span className="font-mono text-neutral-200">{String(frame.right)}</span>
        </span>
        <span>line <span className="font-mono text-neutral-200">{frame.lineno}</span></span>
      </div>

      {/* Array cells */}
      <div className="flex flex-1 flex-wrap content-center items-center justify-center gap-2 py-2">
        {cells.map((val, i) => {
          const isLeft = i === frame.left;
          const isRight = i === frame.right;
          return (
            <motion.div
              key={i}
              animate={{
                backgroundColor: isLeft ? LEFT : isRight ? RIGHT : DEFAULT_CELL,
                scale: isLeft || isRight ? 1.15 : 1,
              }}
              transition={{ duration: 0.25 }}
              className="flex h-12 w-12 items-center justify-center rounded-md border border-neutral-700 font-mono text-sm text-neutral-100"
            >
              {val}
            </motion.div>
          );
        })}
      </div>

      {/* Infinite-loop demo badge — intentionally surfaced, not hidden. */}
      {haltedInfiniteLoop && (
        <div className="rounded-md border border-red-500/50 bg-red-500/10 px-3 py-2 text-center text-sm text-red-300">
          Execution halted at iteration {trace.length}. Pointer state never converged.
        </div>
      )}

      {/* Controls */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <button
            onClick={stepBack}
            disabled={currentIdx === 0}
            className="rounded-md border border-neutral-700 px-3 py-1.5 text-sm text-neutral-200 hover:border-neutral-500 disabled:opacity-40"
            aria-label="Step back"
          >
            ◀ Step
          </button>
          <button
            onClick={togglePlay}
            className="rounded-md px-4 py-1.5 text-sm font-medium text-black"
            style={{ backgroundColor: LEFT }}
          >
            {isPlaying ? "⏸ Pause" : atEnd ? "↻ Replay" : "▶ Play"}
          </button>
          <button
            onClick={stepForward}
            disabled={atEnd}
            className="rounded-md border border-neutral-700 px-3 py-1.5 text-sm text-neutral-200 hover:border-neutral-500 disabled:opacity-40"
            aria-label="Step forward"
          >
            Step ▶
          </button>
        </div>

        <div className="flex items-center gap-1">
          {(["slow", "normal", "fast"] as Speed[]).map((s) => (
            <button
              key={s}
              onClick={() => setSpeed(s)}
              aria-pressed={speed === s}
              className={`rounded-md border px-2.5 py-1 text-xs capitalize ${
                speed === s
                  ? "border-neutral-400 bg-neutral-800 text-neutral-100"
                  : "border-neutral-800 text-neutral-500 hover:border-neutral-600"
              }`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
