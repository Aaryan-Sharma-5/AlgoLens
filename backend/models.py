"""Pydantic v2 models for the AlgoLens API.

GradeRequest is the POST /grade body. ViolationReport and TraceFrame document
the shapes that flow over the SSE stream (checker.py violation dicts and the
instrumenter.py trace frames respectively) — the stream itself serializes the
raw dicts, these models are the typed contract for them.
"""

from pydantic import BaseModel, Field


class GradeRequest(BaseModel):
    """Body of POST /grade. Python source only; pattern selects the checker."""

    source: str
    pattern: str
    array: list[int] = Field(default_factory=list)
    target: int = 0


class ViolationReport(BaseModel):
    """One AST contract violation — matches checker.check_contract() output."""

    type: str
    lineno: int
    severity: str
    label: str


class TraceFrame(BaseModel):
    """One captured loop iteration — matches instrumenter.TraceInjector output."""

    left: int | None = None
    right: int | None = None
    arr_snapshot: list[int] | None = None
    lineno: int
