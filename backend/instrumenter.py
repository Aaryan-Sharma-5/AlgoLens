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
_NON_POINTER_NAMES = {"target", "k", "result", "count"}

# Internal global names used to invoke the submitted function inside the sandbox.
_INPUT_GLOBAL = "__algolens_input__"
_TARGET_GLOBAL = "__algolens_target__"


# --------------------------------------------------------------------------- #
# Payload validation + typing-import stripping
# --------------------------------------------------------------------------- #

def strip_typing_imports(source: str) -> str:
    """Remove `from typing import ...` and `import typing` lines.

    Every default LeetCode Python template starts with `from typing import List`.
    The sandbox blocks `import`, so an unstripped submission would crash with
    ImportError before the student's logic ever runs.

    The import line is blanked in place (content removed, newline kept) rather
    than deleted, so line numbers stay aligned with the original source — the
    trace frames' `lineno` must agree with checker.py's violation line numbers.
    """
    source = re.sub(r'^[ \t]*from[ \t]+typing[ \t]+import[ \t]+[^\n]*', '', source, flags=re.MULTILINE)
    source = re.sub(r'^[ \t]*import[ \t]+typing[ \t]*$', '', source, flags=re.MULTILINE)
    return source


def validate_payload(source: str, input_array: list) -> dict | None:
    """Pre-execution guard. Returns an error dict on rejection, else None."""
    if len(source.encode()) > MAX_SOURCE_BYTES:
        return {"error": "source_too_large", "trace": []}
    if len(input_array) > MAX_ARRAY_LEN:
        return {"error": "array_too_large", "trace": []}
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
        if isinstance(node, ast.Assign) and _is_pointer_init(node.value, array_var):
            for target in node.targets:
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
        return (
            "__trace__.append({"
            f"{self._field('left', self.left_var)}, "
            f"{self._field('right', self.right_var)}, "
            f"{self._snapshot_field()}, "
            f"'lineno': {lineno}"
            "})"
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
        _INPUT_GLOBAL: input_array,
        _TARGET_GLOBAL: target,
        "target": target,
    }
    array_var = names.get("array")
    if array_var:
        g[array_var] = input_array
    return g


def _execute_instrumented(source: str, input_array: list, target, names: dict) -> list:
    """Run the instrumented submission, returning whatever trace was collected.

    Benign runtime errors in the student's code do not discard the partial trace;
    MemoryError is re-raised so the caller can report `memory_limit`.
    """
    code = _instrument_and_compile(source, input_array, target, names)
    g = _build_sandbox_globals(input_array, target, names)
    try:
        exec(code, g)
    except MemoryError:
        raise
    except Exception:
        # Capture-as-far-as-it-got: the trace already lives in g["__trace__"].
        pass
    return g["__trace__"]


def _sandbox_child(source, input_array, target, names, result_queue):
    """Child-process entry point. Caps memory (Linux) then executes."""
    try:
        import resource  # POSIX only; absent on Windows
        resource.setrlimit(
            resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES)
        )
    except Exception:
        pass  # Windows / unsupported — rely on the wall-clock timeout instead.

    try:
        trace = _execute_instrumented(source, input_array, target, names)
        result_queue.put({"error": None, "trace": trace})
    except MemoryError:
        result_queue.put({"error": "memory_limit", "trace": []})
    except Exception:
        result_queue.put({"error": "runtime_error", "trace": []})


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
        # Still running past its budget → treat as a non-converging loop.
        proc.terminate()
        proc.join()
        return {"error": "infinite_loop", "trace": []}

    proc.join(1)
    if proc.is_alive():
        proc.terminate()
        proc.join()
    return result
