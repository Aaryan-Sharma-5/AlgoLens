"""LLM layer for AlgoLens (Groq backend).

Two responsibilities (per the architecture in CLAUDE.md):

1. ``llm_generate`` — ONE blocking, non-streaming call that returns the
   explanation text, the adversarial input that re-triggers the flagged
   violation, and the Socratic follow-up questions. Because Groq's
   ``response_format={"type": "json_object"}`` guarantees valid JSON but NOT the
   field shape, the result is validated and the call retried once before raising
   a ``ValueError`` — so a malformed response can never reach the sandbox as an
   empty adversarial array (which would render a zero-frame trace).

2. ``stream_explanation_from_string`` — chunk-yields the explanation that
   ``llm_generate`` ALREADY produced, to approximate token streaming. This avoids
   a second Groq call: it halves token usage and latency, and removes any chance
   of a second explanation diverging from the structured one.

The LLM never detects violations (that is the deterministic AST job in
checker.py) and never emits code — for the adversarial case it emits only a
data structure (``{"array": [...], "target": int}``).

Model: llama-3.3-70b-versatile on Groq (free tier, ~800ms structured call
latency). Both public entry points are ``async`` because main.py awaits
``llm_generate`` and ``async for``-iterates the explanation stream.
"""

import asyncio
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
    "non_halving_search": (
        "Generate a large SORTED array where the target value sits at the very last "
        "position, so a linear bound advance must scan almost every element before "
        "finding it — exposing the O(N) cost a real binary search would avoid."
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
_BANNED_WORDS = (
    "nested", "slice", "sort", "sorted", "membership",
    "halve", "halving", "midpoint", "bisect", "binary",
)

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


def _validate_generate(data: object) -> dict:
    """Enforce the response shape json_object mode does not guarantee.

    Fatal (raises ValueError → triggers a retry) only when a field the pipeline
    genuinely needs is unusable. The adversarial array is the critical one: an
    empty/invalid array would produce a zero-frame trace. `target` and `q2` are
    coerced rather than treated as fatal.
    """
    if not isinstance(data, dict):
        raise ValueError("llm_schema: response was not a JSON object")

    explanation = data.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip():
        raise ValueError("llm_schema: missing a non-empty 'explanation'")

    adv = data.get("adversarial_input")
    if not isinstance(adv, dict):
        raise ValueError("llm_schema: missing 'adversarial_input' object")
    arr = adv.get("array")
    if (
        not isinstance(arr, list)
        or len(arr) == 0
        or not all(isinstance(x, int) and not isinstance(x, bool) for x in arr)
    ):
        raise ValueError(
            "llm_schema: 'adversarial_input.array' must be a non-empty list of integers"
        )
    target = adv.get("target")
    adv["target"] = target if isinstance(target, int) and not isinstance(target, bool) else 0

    soc = data.get("socratic")
    if not isinstance(soc, dict) or not isinstance(soc.get("q1"), str) or not soc["q1"].strip():
        raise ValueError("llm_schema: missing 'socratic.q1'")
    if not isinstance(soc.get("q2"), str):
        soc["q2"] = ""

    return data


def _build_messages(source: str, violations: list[dict]) -> list[dict]:
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

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def llm_generate(source: str, violations: list[dict]) -> dict:
    """One structured, non-streaming call with shape validation + one retry.

    Returns:
        {
          "explanation": str,
          "adversarial_input": {"array": list[int], "target": int},
          "socratic": {"q1": str, "q2": str},
        }

    Raises ValueError ("llm_schema: ...") if the response is still malformed
    after the retry, so the caller can surface a specific banner.
    """
    messages = _build_messages(source, violations)

    last_error: Exception | None = None
    for _attempt in range(2):  # initial call + one retry
        response = await _client().chat.completions.create(
            model=MODEL,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.3,  # deterministic enough to reliably re-trigger the violation
        )
        try:
            data = json.loads(response.choices[0].message.content)
            return _validate_generate(data)
        except (json.JSONDecodeError, ValueError) as e:
            last_error = e
            continue

    raise ValueError(f"llm_schema: invalid LLM response after retry — {last_error}")


async def stream_explanation_from_string(
    explanation: str, chunk_size: int = 4
) -> AsyncGenerator[str, None]:
    """Chunk-yield a pre-generated explanation to approximate token streaming.

    No second Groq call — the explanation comes straight from ``llm_generate``,
    so the streamed text can never diverge from the structured-call explanation,
    and we save one API request per grade.
    """
    for i in range(0, len(explanation), chunk_size):
        yield explanation[i : i + chunk_size]
        await asyncio.sleep(0.02)
