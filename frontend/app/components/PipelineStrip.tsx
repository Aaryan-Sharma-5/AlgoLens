"use client";

// Visible four-stage agent pipeline. Pure render of a plain { ast, adv, sandbox,
// explain } object — no state machine. The page drives the states from SSE events.

import { Fragment } from "react";

export type StageState = "idle" | "active" | "done" | "error";

export interface PipelineState {
  ast: StageState;
  adv: StageState;
  sandbox: StageState;
  explain: StageState;
}

export const INITIAL_PIPELINE: PipelineState = {
  ast: "idle",
  adv: "idle",
  sandbox: "idle",
  explain: "idle",
};

const STAGES: { key: keyof PipelineState; label: string }[] = [
  { key: "ast", label: "AST Check" },
  { key: "adv", label: "Adversarial Input" },
  { key: "sandbox", label: "Sandbox Run" },
  { key: "explain", label: "Explanation" },
];

const ACCENT = "#C97832";
const ERROR = "#ef4444";

function stageClasses(state: StageState): string {
  const base =
    "rounded-md border px-3 py-1.5 text-xs font-medium whitespace-nowrap transition-colors";
  switch (state) {
    case "done":
      return `${base} border-green-500/50 bg-green-500/10 text-green-400`;
    case "active":
      return `${base} animate-pulse`;
    case "error":
      return base;
    default:
      return `${base} border-neutral-800 text-neutral-500`;
  }
}

function stageInlineStyle(state: StageState): React.CSSProperties {
  if (state === "active")
    return { borderColor: ACCENT, color: ACCENT, backgroundColor: "rgba(201,120,50,0.10)" };
  if (state === "error")
    return { borderColor: ERROR, color: ERROR, backgroundColor: "rgba(239,68,68,0.10)" };
  return {};
}

function stageMark(state: StageState): string {
  if (state === "done") return " ✓";
  if (state === "error") return " ✕";
  return "";
}

export default function PipelineStrip({ pipeline }: { pipeline: PipelineState }) {
  return (
    <div className="mb-5 flex flex-wrap items-center gap-2">
      {STAGES.map((stage, i) => {
        const state = pipeline[stage.key];
        return (
          <Fragment key={stage.key}>
            <div className={stageClasses(state)} style={stageInlineStyle(state)}>
              {stage.label}
              {stageMark(state)}
            </div>
            {i < STAGES.length - 1 && (
              <span className="text-neutral-600">→</span>
            )}
          </Fragment>
        );
      })}
    </div>
  );
}
