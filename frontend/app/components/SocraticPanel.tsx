"use client";

// Renders the Socratic follow-up questions with a gated reveal: q1 shows
// immediately, q2 stays hidden behind an "I've thought about it" button so the
// student engages with the first question independently. If q2 is empty (a
// single, minor-severity question), the button is not shown.
//
// The page remounts this component (via a changing `key`) per grade, so the
// `revealed` state resets without a setState-in-effect.

import { useState } from "react";
import type { Socratic } from "../lib/grading-stream";

interface SocraticPanelProps {
  socratic: Socratic | null;
}

const ACCENT = "#C97832";

function QuestionRow({ index, text }: { index: number; text: string }) {
  return (
    <li className="flex gap-3 text-sm leading-relaxed text-neutral-200">
      <span
        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold text-black"
        style={{ backgroundColor: ACCENT }}
      >
        {index}
      </span>
      <span>{text}</span>
    </li>
  );
}

export default function SocraticPanel({ socratic }: SocraticPanelProps) {
  const [revealed, setRevealed] = useState(false);

  if (!socratic) return null;

  const q1 = socratic.q1?.trim() ? socratic.q1 : "";
  const q2 = socratic.q2?.trim() ? socratic.q2 : "";
  if (!q1) return null;

  return (
    <div
      className="rounded-lg border p-4"
      style={{ borderColor: "rgba(201,120,50,0.4)" }}
    >
      <h2
        className="mb-3 text-xs font-semibold uppercase tracking-wider"
        style={{ color: ACCENT }}
      >
        Think it through
      </h2>

      <ol className="space-y-3">
        <QuestionRow index={1} text={q1} />
        {q2 && revealed && <QuestionRow index={2} text={q2} />}
      </ol>

      {q2 && !revealed && (
        <button
          onClick={() => setRevealed(true)}
          className="mt-4 rounded-md border px-3 py-1.5 text-xs font-medium"
          style={{ borderColor: ACCENT, color: ACCENT }}
        >
          I&apos;ve thought about it →
        </button>
      )}
    </div>
  );
}
