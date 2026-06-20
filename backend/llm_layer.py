"""LLM layer for AlgoLens (Groq backend).

Two responsibilities, kept as two distinct Groq calls (per the architecture in
CLAUDE.md):

1. ``llm_generate`` — ONE blocking, non-streaming call that returns the
   explanation text, the adversarial input that re-triggers the flagged
   violation, and the Socratic follow-up questions. Fired first so that
   ``adversarial_input`` and ``socratic`` are available immediately to the SSE
   pipeline.

2. ``stream_explanation`` — a SECOND streaming call that streams only the
   explanation prose token by token, alongside the trace animation.

The LLM never detects violations (that is the deterministic AST job in
checker.py) and never emits code — for the adversarial case it emits only a
data structure (``{"array": [...], "target": int}``).

Model: llama-3.3-70b-versatile on Groq (free tier, ~800ms structured call
latency). Groq's SDK is OpenAI-compatible: it has no Anthropic-style
``output_config.format`` JSON-schema enforcement, so we use the OpenAI-style
``response_format={"type": "json_object"}`` plus an explicit JSON instruction in
the system prompt.

Both public entry points are ``async`` because main.py awaits ``llm_generate``
and ``async for``-iterates ``stream_explanation`` (see CLAUDE.md SSE stage 2/4),
so only the AsyncGroq client is needed.
"""

import json
import os
from typing import AsyncGenerator

from groq import AsyncGroq

from .checker import SEVERITY, VIOLATION_LABELS

MODEL = "llama-3.3-70b-versatile"   # Groq free tier, ~800ms structured call latency

# Severity → ranking, used to pick the primary violation when several are flagged.
_SEVERITY_RANK = {"critical": 3, "major": 2, "minor": 1}

# Per-violation adversarial-input constraint (verbatim intent from CLAUDE.md).
ADVERSARIAL_CONSTRAINTS = {
    "linear_membership_check": (
        "Generate an array with a large number of duplicate values positioned at "
        "the very end of the search space to force worst-case linear scan cost."
    ),
    "implicit_slice_loop": (
        "Generate an array where the valid window spans the entire array length, "
        "maximizing the number of slice operations performed."
    ),
    "nested_loop": (
        "Generate an array with maximum length where no early-exit condition is met, "
        "forcing full O(N^2) traversal."
    ),
    "sort_in_loop": (
        "Generate an array that is reverse-sorted so every sort call does maximum work."
    ),
}

# Severity-routed explanation depth.
_EXPLANATION_DEPTH = {
    "critical": "Write a detailed paragraph of 4-6 sentences.",
    "major": "Write a medium-length explanation of 2-3 sentences.",
    "minor": "Write a short explanation of 1-2 sentences.",
}

# Severity-routed Socratic question count.
_QUESTION_COUNT = {"critical": 2, "major": 2, "minor": 1}

# Words that would name the bug / hint the fix — banned from Socratic questions.
_BANNED_WORDS = ("nested", "slice", "sort", "sorted", "membership")

# Lazily-constructed shared async client so importing this module never requires
# GROQ_API_KEY to be set (it is only needed when a call is actually made).
_async_client: AsyncGroq | None = None


def _client() -> AsyncGroq:
    global _async_client
    if _async_client is None:
        _async_client = AsyncGroq(api_key=os.environ["GROQ_API_KEY"])
    return _async_client


def _primary_violation(violations: list[dict]) -> dict:
    """The most severe violation drives explanation depth + adversarial input."""
    return max(violations, key=lambda v: _SEVERITY_RANK.get(v.get("severity"), 0))


def _depth_instruction(severity: str) -> str:
    return _EXPLANATION_DEPTH.get(severity, _EXPLANATION_DEPTH["minor"])


def _label(violation: dict) -> str:
    return violation.get("label") or VIOLATION_LABELS.get(violation["type"], violation["type"])


def _violation_summary(violations: list[dict]) -> str:
    return "\n".join(
        f"- line {v.get('lineno', '?')}: {_label(v)} (severity: {v['severity']})"
        for v in violations
    )


async def llm_generate(source: str, violations: list[dict]) -> dict:
    """One structured, non-streaming call.

    Returns:
        {
          "explanation": str,
          "adversarial_input": {"array": list[int], "target": int},
          "socratic": {"q1": str, "q2": str},
        }
    """
    primary = _primary_violation(violations)
    severity = primary["severity"]
    n_questions = _QUESTION_COUNT.get(severity, 1)
    adversarial_constraint = ADVERSARIAL_CONSTRAINTS.get(primary["type"], "")
    banned = ", ".join(_BANNED_WORDS)
    q2_rule = (
        "Set q2 to an empty string."
        if n_questions == 1
        else "Provide a second distinct question in q2."
    )

    system = f"""You are a CS pedagogy engine — a Socratic assessor, not a linter. Respond ONLY with valid JSON. No markdown, no backticks, no preamble. Exact schema:
{{"explanation": "...", "adversarial_input": {{"array": [...], "target": 0}}, "socratic": {{"q1": "...", "q2": "..."}}}}
q2 may be an empty string when only one Socratic question is requested.
Do NOT reveal the fix in any field. Do NOT use the words: {banned}."""

    user = f"""A student submitted this Python solution. An AST verifier already found these pattern-contract violations:
{_violation_summary(violations)}

Source code:
```python
{source}
```

Primary violation: {_label(primary)} (severity: {severity})

explanation: {_depth_instruction(severity)} In plain English, explain why this violation makes the solution slower than the pattern's contract promises. Do not paste code and do not prescribe the exact fix.

adversarial_input: {adversarial_constraint} Generate an adversarial_input array of at least 8 integer elements (large enough to demonstrate the cost, small enough to animate). Emit ONLY the data structure, never code. Choose target so the function actually runs its loops over this input.

socratic: Provide {n_questions} Socratic question(s) in q1{' and q2' if n_questions > 1 else ''}. {q2_rule} Each question must probe WHY the code's cost is hidden — make the student reason about it — not WHAT to change. Do not name the bug or the violation."""

    response = await _client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,  # deterministic enough to reliably re-trigger the violation
    )

    return json.loads(response.choices[0].message.content)


async def stream_explanation(
    source: str, violations: list[dict]
) -> AsyncGenerator[str, None]:
    """Second call: stream ONLY the explanation prose, token by token."""
    primary = _primary_violation(violations)
    severity = primary["severity"]

    system = "You are a CS pedagogy engine explaining algorithm violations to a student."
    user = f"""{_depth_instruction(severity)} Explain this violation in plain English. Do not reveal the fix; write only the explanation prose with no headings or code.
Violation: {_label(primary)}
Severity: {severity}
Code:
```python
{source}
```"""

    stream = await _client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.4,
        stream=True,
    )
    async for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta
