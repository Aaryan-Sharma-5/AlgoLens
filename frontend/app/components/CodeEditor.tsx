"use client";

// Monaco requires `window`, so it is dynamically imported with ssr:false. Per the
// Next 16 docs, ssr:false only works inside a Client Component — keeping it here
// isolates the SSR-disable to this component instead of the whole page.

import { useEffect, useRef } from "react";
import dynamic from "next/dynamic";
import type { OnMount, Monaco } from "@monaco-editor/react";
import type { editor } from "monaco-editor";
import type { Violation } from "../lib/grading-stream";

const Editor = dynamic(
  () => import("@monaco-editor/react").then((m) => m.Editor),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-full items-center justify-center text-sm text-neutral-500">
        Loading editor…
      </div>
    ),
  },
);

interface CodeEditorProps {
  value: string;
  onChange: (value: string) => void;
  violations: Violation[];
  // The line currently executing in the TraceAnimator (1-based), or null. Drives the pulsing execution highlight that syncs the editor to the animation.
  activeLine?: number | null;
}

export default function CodeEditor({
  value,
  onChange,
  violations,
  activeLine,
}: CodeEditorProps) {
  const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null);
  const monacoRef = useRef<Monaco | null>(null);
  const decorationIds = useRef<string[]>([]);
  const activeDecoIds = useRef<string[]>([]);

  function applyDecorations(items: Violation[]) {
    const ed = editorRef.current;
    const monaco = monacoRef.current;
    if (!ed || !monaco) return;

    const decorations = items.map((v) => ({
      range: new monaco.Range(v.lineno, 1, v.lineno, 1),
      options: {
        isWholeLine: true,
        // Semantic violation — NOT a syntax error squiggle. Background only.
        className: v.severity === "critical" ? "line-critical" : "line-major",
        glyphMarginClassName:
          v.severity === "critical" ? "glyph-critical" : "glyph-major",
        hoverMessage: { value: v.label },
      },
    }));

    decorationIds.current = ed.deltaDecorations(
      decorationIds.current,
      decorations,
    );
  }

  const handleMount: OnMount = (ed, monaco) => {
    editorRef.current = ed;
    monacoRef.current = monaco;
    applyDecorations(violations);
  };

  // Re-apply decorations whenever the violation set changes.
  useEffect(() => {
    applyDecorations(violations);
  }, [violations]);

  // Bidirectional execution sync: highlight the line the animator is currently executing. Kept in a SEPARATE decoration set so it never clobbers the violation decorations on the same line.
  useEffect(() => {
    const ed = editorRef.current;
    const monaco = monacoRef.current;
    if (!ed || !monaco) return;
    const decos = activeLine
      ? [
          {
            range: new monaco.Range(activeLine, 1, activeLine, 1),
            options: {
              isWholeLine: true,
              className: "line-exec-active",
              glyphMarginClassName: "glyph-exec-active",
            },
          },
        ]
      : [];
    activeDecoIds.current = ed.deltaDecorations(activeDecoIds.current, decos);
  }, [activeLine]);

  return (
    <div className="h-full w-full overflow-hidden rounded-lg border border-neutral-800">
      {/* Decoration styles are global (Monaco renders className-only elements). */}
      <style>{`
        .line-critical { background-color: rgba(239, 68, 68, 0.16); }
        .line-major    { background-color: rgba(249, 115, 22, 0.16); }
        .glyph-critical, .glyph-major {
          margin-left: 5px;
          width: 6px !important;
          border-radius: 9999px;
        }
        .glyph-critical { background-color: #ef4444; }
        .glyph-major    { background-color: #f97316; }
        .line-exec-active { animation: execpulse 1.2s ease-in-out infinite; }
        @keyframes execpulse {
          0%, 100% { background-color: rgba(201, 120, 50, 0.10); }
          50%      { background-color: rgba(201, 120, 50, 0.30); }
        }
        .glyph-exec-active {
          margin-left: 5px;
          width: 6px !important;
          border-radius: 9999px;
          background-color: #C97832;
        }
      `}</style>
      <Editor
        height="100%"
        language="python"
        theme="vs-dark"
        value={value}
        onChange={(v) => onChange(v ?? "")}
        onMount={handleMount}
        options={{
          fontSize: 14,
          minimap: { enabled: false },
          glyphMargin: true,
          scrollBeyondLastLine: false,
          automaticLayout: true,
          padding: { top: 14, bottom: 14 },
          tabSize: 4,
          renderLineHighlight: "none",
          fontFamily: "var(--font-geist-mono), monospace",
        }}
      />
    </div>
  );
}
