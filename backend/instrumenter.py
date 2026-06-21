"""Execution-trace instrumenter + sandbox for AlgoLens.

Pipeline (all deterministic, no test runner):

    validate_payload(source, input_array)   # pre-execution guard
    strip_typing_imports(source)            # remove `from typing import ...`
    extract_pointer_names(tree)             # signature-based name resolution
    infer_pointer_vars(tree, array_var)     # body-based fallback
    TraceInjector(...)                      # prepend __trace__.append(...) into loops
    sandboxed_run(source, input_array, ...) # single, resource-capped child execution

Variable names are resolved dynamically (Layer 2 of the spec) — no user
compliance with a fixed naming convention is required.
"""

import __future__
import ast
import builtins
import multiprocessing
import queue
import re
import typing

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

MAX_SOURCE_BYTES = 50_000
MAX_ARRAY_LEN = 10_000
MEMORY_LIMIT_BYTES = 128 * 1024 * 1024  # 128 MB child RLIMIT_AS (Linux only)
DEFAULT_TIMEOUT = 3

# Hard cap on captured frames. Each frame deep-copies the array, so an
# uncapped loop over a large array is O(N^2) memory — enough to blow RLIMIT_AS
# on legitimate code and to produce a multi-hundred-MB SSE payload. 500 frames
# is more than enough to animate while keeping memory and the payload bounded.
MAX_FRAMES = 500
_MAX_FRAMES_GLOBAL = "__algolens_max_frames__"

# Extra wall-clock grace for process spawn + module import before we treat a
# silent child as an infinite loop. The execution budget itself is `timeout`.
# Spawn + import measures well under a second; 1.0s is a safe margin.
_STARTUP_GRACE = 1.0

# __builtins__ whitelist exposed to submitted code.
_BUILTIN_WHITELIST = (
    "range", "len", "list", "enumerate", "min", "max", "abs",
    "sum", "sorted", "zip", "isinstance", "int", "str", "bool", "float",
)

# Names that are initialized to an int but are accumulators/targets, not pointers.
# (Pointers in these patterns are almost always i/j/left/right/lo/hi; these names
# are sums, results, or running counters and must not be mistaken for pointers.)
_NON_POINTER_NAMES = {
    "target", "k", "result", "count",
    "ans", "res", "ret", "out", "output", "total", "curr", "cur", "prev",
}

# Internal global names used to invoke the submitted function inside the sandbox.
_INPUT_GLOBAL = "__algolens_input__"
_TARGET_GLOBAL = "__algolens_target__"


# --------------------------------------------------------------------------- #
# Payload validation + typing-import stripping
# --------------------------------------------------------------------------- #

# Dunder attributes/names that enable sandbox escape via object traversal
# (e.g. ().__class__.__base__.__subclasses__()[...].__init__.__globals__).
# None of these require an import, so withholding __import__ is not enough.
_FORBIDDEN_DUNDERS = {
    "__class__", "__bases__", "__base__", "__subclasses__", "__mro__",
    "__globals__", "__builtins__", "__dict__", "__getattribute__",
    "__subclasshook__", "__init_subclass__", "__code__", "__closure__",
}


class _SafetyVisitor(ast.NodeVisitor):
    """Reject dunder attribute/name access in submitted code.

    Raises ValueError on the first forbidden access. Run on the ORIGINAL
    student AST, before trace injection adds the internal `__trace__` name.
    """

    def visit_Attribute(self, node: ast.Attribute) -> None:
        attr = node.attr
        if attr in _FORBIDDEN_DUNDERS or (attr.startswith("__") and attr.endswith("__")):
            raise ValueError(f"unsafe_code: attribute {attr!r} is not allowed")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        ident = node.id
        if ident.startswith("__") and ident.endswith("__"):
            raise ValueError(f"unsafe_code: name {ident!r} is not allowed")
        self.generic_visit(node)


def strip_typing_imports(source: str) -> str:
    """Remove `from typing import ...` and `import typing` lines.

    Every default LeetCode Python template starts with `from typing import List`.
    The sandbox blocks `import`, so an unstripped submission would crash with
    ImportError before the student's logic ever runs.

    The import line is blanked in place (content removed, newline kept) rather
    than deleted, so line numbers stay aligned with the original source — the
    trace frames' `lineno` must agree with checker.py's violation line numbers.
    """
    # Parenthesized multi-line form FIRST: `from typing import (\n  List,\n  Dict,\n)`.
    # Replace with the same number of blank lines (preserve newline count) so trace
    # `lineno`s stay aligned with checker.py's violation line numbers.
    source = re.sub(
        r'^[ \t]*from[ \t]+typing[ \t]+import[ \t]*\([^)]*\)',
        lambda m: '\n' * m.group(0).count('\n'),
        source, flags=re.MULTILINE | re.DOTALL,
    )
    # Single-line `from typing import ...` and `import typing[ as t]`.
    source = re.sub(r'^[ \t]*from[ \t]+typing[ \t]+import[ \t]+[^\n]*', '', source, flags=re.MULTILINE)
    source = re.sub(r'^[ \t]*import[ \t]+typing\b[^\n]*', '', source, flags=re.MULTILINE)
    return source


def validate_payload(source: str, input_array: list) -> dict | None:
    """Pre-execution guard. Returns an error dict on rejection, else None."""
    if len(source.encode()) > MAX_SOURCE_BYTES:
        return {"error": "source_too_large", "trace": []}
    if len(input_array) > MAX_ARRAY_LEN:
        return {"error": "array_too_large", "trace": []}

    # Static safety pass: block dunder-traversal sandbox escapes before exec.
    # A SyntaxError here is left for the stage-1 checker / compile to surface.
    try:
        _SafetyVisitor().visit(ast.parse(strip_typing_imports(source)))
    except ValueError:
        return {"error": "unsafe_code", "trace": []}
    except SyntaxError:
        pass
    return None


# --------------------------------------------------------------------------- #
# AST helpers
# --------------------------------------------------------------------------- #

def _first_funcdef(tree: ast.AST) -> ast.FunctionDef | None:
    return next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)),
        None,
    )


def _param_names(func: ast.FunctionDef) -> list[str]:
    return [a.arg for a in (list(func.args.posonlyargs) + list(func.args.args))]


def _is_used_as_index(func: ast.AST, varname: str) -> bool:
    """True if `varname` appears inside any subscript's index/slice (arr[var])."""
    if not varname:
        return False
    for node in ast.walk(func):
        if isinstance(node, ast.Subscript):
            for sub in ast.walk(node.slice):
                if isinstance(sub, ast.Name) and sub.id == varname:
                    return True
    return False


def _is_pointer_init(value: ast.AST, array_var: str | None) -> bool:
    """Pointer initializers look like `0` (int constant) or `len(arr) - 1`."""
    if isinstance(value, ast.Constant) and isinstance(value.value, int) and not isinstance(value.value, bool):
        return True
    # Any expression referencing `len(...)` — covers `len(arr) - 1`, `len(arr)`.
    for sub in ast.walk(value):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == "len":
            return True
    return False


# --------------------------------------------------------------------------- #
# Pointer-name resolution
# --------------------------------------------------------------------------- #

def extract_pointer_names(tree: ast.AST) -> dict:
    """Resolve names from the function signature.

    First param -> array variable. Subsequent params count as pointers only if
    they are actually used as subscript indices (`arr[param]`); a `target`/`k`
    param therefore does not get mistaken for a pointer. Anything left unresolved
    is filled in later by `infer_pointer_vars`.
    """
    names = {"array": None, "left": None, "right": None}
    func = _first_funcdef(tree)
    if func is None:
        return names

    params = _param_names(func)
    if params:
        names["array"] = params[0]

    pointer_params = [p for p in params[1:] if _is_used_as_index(func, p)]
    if len(pointer_params) >= 1:
        names["left"] = pointer_params[0]
    if len(pointer_params) >= 2:
        names["right"] = pointer_params[1]
    return names


def infer_pointer_vars(tree: ast.AST, array_var: str | None) -> dict:
    """Resolve pointers declared inside the function body.

    Walks the body for integer-initialized assignments, excludes known
    non-pointer names (`target`, `k`, `result`, `count`) and the array itself,
    and keeps only variables that are ever used as a subscript index (`arr[var]`)
    — this distinguishes pointers from counters/accumulators that also start at 0.
    """
    result = {"left": None, "right": None}
    func = _first_funcdef(tree)
    if func is None:
        return result

    exclude = set(_NON_POINTER_NAMES)
    if array_var:
        exclude.add(array_var)

    candidates: list[str] = []
    for node in ast.walk(func):
        # Pointer-initialized assignments (`left = 0`, `right = len(arr) - 1`)
        # AND for-loop targets (`for right in range(len(arr))`) both qualify —
        # the loop variable is the sweeping pointer in the canonical sliding
        # window, and it is NOT an ast.Assign, so it must be picked up here.
        targets: list = []
        if isinstance(node, ast.Assign) and _is_pointer_init(node.value, array_var):
            targets = node.targets
        elif isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Name)
                and target.id not in exclude
                and target.id not in candidates
                and _is_used_as_index(func, target.id)
            ):
                candidates.append(target.id)

    if candidates:
        result["left"] = candidates[0]
    if len(candidates) >= 2:
        result["right"] = candidates[1]
    return result


def resolve_names(source: str) -> dict:
    """Full two-layer resolution: signature first, body inference for gaps."""
    tree = ast.parse(strip_typing_imports(source))
    names = extract_pointer_names(tree)
    if not names.get("left") or not names.get("right"):
        inferred = infer_pointer_vars(tree, names.get("array"))
        if not names.get("left"):
            names["left"] = inferred.get("left")
        if not names.get("right"):
            names["right"] = inferred.get("right")
    return names


# --------------------------------------------------------------------------- #
# Trace injection
# --------------------------------------------------------------------------- #

class TraceInjector(ast.NodeTransformer):
    """Prepend `__trace__.append({...})` at the START of every loop body.

    Prepending (index 0) — never appending — guarantees the iteration is
    recorded before any `continue`, `break`, or `return` can bypass the capture.
    Captured fields: resolved left/right pointer values, a shallow copy of the
    array (`arr_snapshot`), and the originating loop `lineno`. The injected names
    are the dynamically resolved variables, not string literals.
    """

    def __init__(self, array_var: str | None, left_var: str | None, right_var: str | None):
        self.array_var = array_var
        self.left_var = left_var
        self.right_var = right_var

    def _field(self, key: str, var: str | None) -> str:
        if not var:
            return f"'{key}': None"
        return f"'{key}': {var} if '{var}' in dir() else None"

    def _snapshot_field(self) -> str:
        if not self.array_var:
            return "'arr_snapshot': None"
        a = self.array_var
        return f"'arr_snapshot': list({a}) if '{a}' in dir() else None"

    def _make_trace_snippet(self, lineno: int) -> str:
        # Guard the append with the frame cap so a long/non-converging loop
        # cannot grow the trace without bound (memory + SSE payload + animation).
        return (
            f"__trace__.append({{"
            f"{self._field('left', self.left_var)}, "
            f"{self._field('right', self.right_var)}, "
            f"{self._snapshot_field()}, "
            f"'lineno': {lineno}"
            f"}}) if len(__trace__) < {_MAX_FRAMES_GLOBAL} else None"
        )

    def visit_For(self, node: ast.For):
        self.generic_visit(node)
        inject = ast.parse(self._make_trace_snippet(node.lineno)).body
        node.body = inject + node.body  # prepend — never append
        return node

    def visit_While(self, node: ast.While):
        self.generic_visit(node)
        inject = ast.parse(self._make_trace_snippet(node.lineno)).body
        node.body = inject + node.body  # prepend — never append
        return node


# --------------------------------------------------------------------------- #
# Sandboxed execution
# --------------------------------------------------------------------------- #

def _instrument_and_compile(source: str, input_array: list, target, names: dict):
    """Strip, parse, inject traces, append the invoking call, and compile.

    Compiled with the PEP 563 future flag so all annotations are lazy: a bare
    annotation like `nums: List[int]` that survives typing-import stripping is
    never evaluated and cannot NameError at def-time. The flag is passed to
    `compile()` directly rather than as a `from __future__ import` statement —
    the statement would run an `IMPORT_NAME` bytecode needing `__import__`, which
    the sandbox builtin whitelist intentionally withholds.
    """
    tree = ast.parse(strip_typing_imports(source))

    injector = TraceInjector(names.get("array"), names.get("left"), names.get("right"))
    tree = injector.visit(tree)
    ast.fix_missing_locations(tree)

    func = _first_funcdef(tree)
    if func is not None:
        # Invoke the function: first param <- input array, any further params <- target.
        n = len(_param_names(func))
        call_args = []
        for idx in range(n):
            name = _INPUT_GLOBAL if idx == 0 else _TARGET_GLOBAL
            call_args.append(ast.Name(id=name, ctx=ast.Load()))
        call = ast.Expr(
            ast.Call(func=ast.Name(id=func.name, ctx=ast.Load()), args=call_args, keywords=[])
        )
        tree.body.append(call)
        ast.fix_missing_locations(tree)

    return compile(
        tree,
        "<algolens_sandbox>",
        "exec",
        flags=__future__.annotations.compiler_flag,
        dont_inherit=True,
    )


def _build_sandbox_globals(input_array: list, target, names: dict) -> dict:
    sandbox_builtins = {name: getattr(builtins, name) for name in _BUILTIN_WHITELIST}
    # `dir` powers the `'<var>' in dir()` guard inside each injected trace frame.
    sandbox_builtins["dir"] = dir

    g = {
        "__builtins__": sandbox_builtins,
        "typing": typing,  # safety net for inline `typing.List` references
        "__trace__": [],
        _MAX_FRAMES_GLOBAL: MAX_FRAMES,
        _INPUT_GLOBAL: input_array,
        _TARGET_GLOBAL: target,
        "target": target,
    }
    array_var = names.get("array")
    if array_var:
        g[array_var] = input_array
    return g


def _sandbox_child(source, input_array, target, names, result_queue):
    """Child-process entry point. Caps memory (Linux) then executes.

    Installs a SIGTERM handler that flushes whatever trace has been captured so
    far. When the parent terminates a non-converging loop it sends SIGTERM, and
    this handler is what lets the `infinite_loop` case ship its partial frames
    (the array of captured iterations) instead of an empty trace — the frame cap
    keeps that flushed list bounded.
    """
    import os
    import signal

    try:
        import resource  # POSIX only; absent on Windows
        resource.setrlimit(
            resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES)
        )
    except Exception:
        pass  # Windows / unsupported — rely on the wall-clock timeout instead.

    # Build globals here so the SIGTERM handler can read the live trace list.
    code = _instrument_and_compile(source, input_array, target, names)
    g = _build_sandbox_globals(input_array, target, names)
    live_trace = g["__trace__"]

    def _flush_on_term(signum, frame):
        try:
            result_queue.put({"error": "infinite_loop", "trace": list(live_trace)})
        except Exception:
            pass
        os._exit(0)

    try:
        signal.signal(signal.SIGTERM, _flush_on_term)
    except (ValueError, OSError):
        pass  # not on main thread / unsupported — parent falls back to [] trace.

    try:
        exec(code, g)
        result_queue.put({"error": None, "trace": list(live_trace)})
    except MemoryError:
        result_queue.put({"error": "memory_limit", "trace": []})
    except Exception:
        # Benign student runtime error — keep whatever was captured.
        result_queue.put({"error": "runtime_error", "trace": list(live_trace)})


def sandboxed_run(source: str, input_array: list, target, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Single, isolated execution of `source` on `input_array`.

    Returns one of:
        {"error": None,            "trace": [frame, ...]}   # success
        {"error": "infinite_loop", "trace": []}             # timed out / never converged
        {"error": "memory_limit",  "trace": []}             # exceeded RLIMIT_AS
        {"error": "<validation>",  "trace": []}             # rejected pre-execution
    """
    rejection = validate_payload(source, input_array)
    if rejection is not None:
        return rejection

    names = resolve_names(source)

    ctx = multiprocessing.get_context("spawn")
    result_queue = ctx.Queue()
    proc = ctx.Process(
        target=_sandbox_child,
        args=(source, input_array, target, names, result_queue),
    )
    proc.start()

    try:
        # Drain the queue first to avoid a join-before-drain feeder deadlock.
        result = result_queue.get(timeout=timeout + _STARTUP_GRACE)
    except queue.Empty:
        # Still running past its budget → non-converging loop. terminate() sends
        # SIGTERM, which the child's handler catches to flush its partial trace;
        # drain that here so the infinite-loop demo can play the captured frames.
        proc.terminate()
        try:
            result = result_queue.get(timeout=0.5)
        except queue.Empty:
            result = {"error": "infinite_loop", "trace": []}
        proc.join()
        return result

    proc.join(1)
    if proc.is_alive():
        proc.terminate()
        proc.join()
    return result
