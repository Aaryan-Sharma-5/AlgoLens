"use client";

// Terminal-style status readout shown in the trace slot while the LLM + sandbox stages run. It masks latency: instead of a static spinner, the judge watches timestamped pipeline events stream in. Lines are pushed by the page as SSE events arrive, so the timestamps are real elapsed milliseconds.

import { useEffect, useRef } from "react";

const ACCENT = "#C97832";

export default function TerminalLoader({ logs }: { logs: string[] }) {
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [logs]);

  return (
    <div className="h-full overflow-hidden rounded-lg border border-neutral-800 bg-black/40 p-4 font-mono text-xs">
      <div className="mb-2 flex items-center gap-1.5 text-neutral-600">
        <span
          className="inline-block h-2.5 w-2.5 rounded-full"
          style={{ backgroundColor: ACCENT }}
        />
        algolens · grading pipeline
      </div>
      <div className="h-[calc(100%-1.75rem)] overflow-y-auto">
        {logs.map((line, i) => {
          const last = i === logs.length - 1;
          return (
            <div key={i} className="leading-relaxed text-neutral-400">
              <span className="text-neutral-600">$ </span>
              <span className={last ? "text-neutral-200" : ""}>{line}</span>
              {last && (
                <span
                  className="ml-1 inline-block h-3 w-1.5 animate-pulse align-middle"
                  style={{ backgroundColor: ACCENT }}
                />
              )}
            </div>
          );
        })}
        <div ref={endRef} />
      </div>
    </div>
  );
}
