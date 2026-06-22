"use client";

// Streams the explanation token by token. `text` is the accumulated string the
// page builds from each `explanation` SSE token; a caret blinks while streaming.

interface ExplanationPanelProps {
  text: string;
  streaming: boolean;
}

export default function ExplanationPanel({
  text,
  streaming,
}: ExplanationPanelProps) {
  if (!text && !streaming) {
    return (
      <div className="rounded-lg border border-neutral-800 p-4 text-sm text-neutral-600">
        The explanation will stream here once you submit.
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-neutral-800 p-4">
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-neutral-500">
        Explanation
      </h2>
      <p className="whitespace-pre-wrap text-sm leading-relaxed text-neutral-200">
        {text}
        {streaming && (
          <span className="ml-0.5 inline-block h-4 w-2 animate-pulse bg-neutral-400 align-middle" />
        )}
      </p>
    </div>
  );
}
