"""AST contract verifier for AlgoLens.

Two deterministic checkers (no test runner, no Big-O inference) that walk a
submission's AST and flag pattern-contract violations. The public entry point is
``check_contract(source, pattern)``.

Supported patterns:
    "sliding_window" -> SlidingWindowChecker
    "two_pointers"   -> TwoPointerChecker
"""

import ast

# Severity map — used by the LLM prompt and the frontend badge color.
SEVERITY = {
    "implicit_slice_loop":      "critical",
    "nested_loop":              "major",
    "sort_in_loop":             "major",
    "linear_membership_check":  "minor",
}

# Human labels — sent to the frontend. Never show raw violation type keys to the user.
VIOLATION_LABELS = {
    "linear_membership_check": "Slow lookup inside loop (O(N²) hidden cost)",
    "implicit_slice_loop":     "Array slice inside loop creates implicit second loop",
    "nested_loop":             "Nested loop detected — expected single pass",
    "sort_in_loop":            "Sort inside loop — O(N² log N) total cost",
}

# Annotation base names that make a `x in container` lookup O(1), so the
# linear_membership_check should NOT fire for them.
_HASHED_LOOKUP_ANNOTATIONS = {"set", "Set", "dict", "Dict"}


def extract_func_args(tree: ast.AST) -> list[tuple[str, str | None]]:
    """Walk the AST, find the first FunctionDef, and return its arguments.

    Returns a list of ``(arg_name, annotation_str)`` where ``annotation_str`` is
    the unparsed source of the type annotation, or ``None`` when the argument is
    unannotated.
    """
    func = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)),
        None,
    )
    if func is None:
        return []

    a = func.args
    # positional-only, normal positional, then keyword-only — vararg/kwarg ignored.
    ordered = list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs)

    result: list[tuple[str, str | None]] = []
    for arg in ordered:
        annotation = ast.unparse(arg.annotation) if arg.annotation is not None else None
        result.append((arg.arg, annotation))
    return result


def _annotation_is_hashed_lookup(annotation: str | None) -> bool:
    """True if the annotation denotes a set/dict (O(1) membership) type."""
    if annotation is None:
        return False
    # Strip any subscript (Set[int] -> Set) and any module prefix (typing.Set -> Set).
    base = annotation.split("[", 1)[0].strip().split(".")[-1]
    return base in _HASHED_LOOKUP_ANNOTATIONS


class SlidingWindowChecker(ast.NodeVisitor):
    """Flags nested_loop, implicit_slice_loop, sort_in_loop."""

    def __init__(self) -> None:
        self.violations: list[dict] = []
        self._loop_depth: int = 0

    def _visit_loop(self, node) -> None:
        # nested_loop: any For/While that is itself already inside a loop.
        if self._loop_depth > 0:
            self.violations.append({"type": "nested_loop", "lineno": node.lineno})
        self._loop_depth += 1
        self.generic_visit(node)
        self._loop_depth -= 1

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop(node)

    def visit_While(self, node: ast.While) -> None:
        self._visit_loop(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        # implicit_slice_loop: arr[i:j] (ast.Slice) evaluated inside a loop.
        if self._loop_depth > 0 and isinstance(node.slice, ast.Slice):
            self.violations.append(
                {"type": "implicit_slice_loop", "lineno": node.lineno}
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # sort_in_loop: .sort() or sorted() called inside a loop.
        # NOTE: only full sorts are flagged. heapq.heappush / heapq.heappop are
        # O(log N) and legitimate inside a loop — do NOT add them here.
        if self._loop_depth > 0:
            func = node.func
            is_sort = (
                (isinstance(func, ast.Attribute) and func.attr == "sort")
                or (isinstance(func, ast.Name) and func.id == "sorted")
            )
            if is_sort:
                self.violations.append({"type": "sort_in_loop", "lineno": node.lineno})
        self.generic_visit(node)


class TwoPointerChecker(SlidingWindowChecker):
    """Everything SlidingWindowChecker flags, plus linear_membership_check.

    ``list_args`` is the set of function-argument names whose type annotation is
    list-like (i.e. NOT set/Set/dict/Dict). The membership check only fires when
    the right-hand comparator of an ``in`` test is one of these names — this is
    the false-positive guard: ``x in some_set`` is O(1) and must not be flagged.
    """

    def __init__(self, list_args: list[str] | None = None) -> None:
        super().__init__()
        self.list_args: list[str] = list_args or []

    def visit_Compare(self, node: ast.Compare) -> None:
        if self._loop_depth > 0:
            for op, comparator in zip(node.ops, node.comparators):
                if (
                    isinstance(op, ast.In)
                    and isinstance(comparator, ast.Name)
                    and comparator.id in self.list_args
                ):
                    self.violations.append(
                        {"type": "linear_membership_check", "lineno": node.lineno}
                    )
        self.generic_visit(node)


def check_contract(source: str, pattern: str) -> list[dict]:
    """Public entry point: parse ``source`` and run the checker for ``pattern``.

    Returns a list of ``{type, lineno, severity, label}`` dicts, one per
    violation, in source-traversal order.
    """
    tree = ast.parse(source)

    if pattern == "sliding_window":
        checker: SlidingWindowChecker = SlidingWindowChecker()
    elif pattern == "two_pointers":
        list_args = [
            name
            for name, annotation in extract_func_args(tree)
            if not _annotation_is_hashed_lookup(annotation)
        ]
        checker = TwoPointerChecker(list_args=list_args)
    else:
        raise ValueError(
            f"Unknown pattern {pattern!r}. Expected 'sliding_window' or 'two_pointers'."
        )

    checker.visit(tree)

    return [
        {
            "type": v["type"],
            "lineno": v["lineno"],
            "severity": SEVERITY[v["type"]],
            "label": VIOLATION_LABELS[v["type"]],
        }
        for v in checker.violations
    ]
