"""Tests for checker.py — one passing and one failing case per violation type.

All fixture code is inline (no file I/O). "Failing case" = code that *should*
trigger the violation; "passing case" = clean code that should not.
"""

from backend.checker import check_contract


def _types(source: str, pattern: str) -> list[str]:
    return [v["type"] for v in check_contract(source, pattern)]


# --------------------------------------------------------------------------- #
# nested_loop (sliding_window)
# --------------------------------------------------------------------------- #

def test_nested_loop_flagged():
    source = """
def f(arr):
    total = 0
    for i in range(len(arr)):
        for j in range(len(arr)):
            total += arr[i] + arr[j]
    return total
"""
    assert "nested_loop" in _types(source, "sliding_window")


def test_nested_loop_clean():
    source = """
def f(arr):
    total = 0
    for x in arr:
        total += x
    return total
"""
    assert "nested_loop" not in _types(source, "sliding_window")


# --------------------------------------------------------------------------- #
# implicit_slice_loop (sliding_window)
# --------------------------------------------------------------------------- #

def test_implicit_slice_loop_flagged():
    source = """
def f(arr, k):
    best = 0
    for i in range(len(arr)):
        window = arr[i:i + k]
        best = max(best, sum(window))
    return best
"""
    assert "implicit_slice_loop" in _types(source, "sliding_window")


def test_implicit_slice_loop_clean():
    source = """
def f(arr, k):
    head = arr[0:k]
    best = sum(head)
    for i in range(k, len(arr)):
        best = max(best, arr[i])
    return best
"""
    # The only slice is outside any loop, so it must not be flagged.
    assert "implicit_slice_loop" not in _types(source, "sliding_window")


# --------------------------------------------------------------------------- #
# sort_in_loop (sliding_window)
# --------------------------------------------------------------------------- #

def test_sort_in_loop_flagged():
    source = """
def f(arr):
    out = []
    for i in range(len(arr)):
        arr.sort()
        out.append(arr[0])
    return out
"""
    assert "sort_in_loop" in _types(source, "sliding_window")


def test_sort_in_loop_flagged_sorted_builtin():
    source = """
def f(arr):
    out = []
    for i in range(len(arr)):
        out.append(sorted(arr)[0])
    return out
"""
    assert "sort_in_loop" in _types(source, "sliding_window")


def test_sort_in_loop_clean():
    source = """
def f(arr):
    arr.sort()
    out = 0
    for x in arr:
        out += x
    return out
"""
    assert "sort_in_loop" not in _types(source, "sliding_window")


# --------------------------------------------------------------------------- #
# linear_membership_check (two_pointers) — incl. set-annotation false-positive fix
# --------------------------------------------------------------------------- #

def test_linear_membership_check_flagged():
    source = """
from typing import List

def two_sum(nums: List[int], target: int) -> bool:
    for i in range(len(nums)):
        if target - nums[i] in nums:
            return True
    return False
"""
    assert "linear_membership_check" in _types(source, "two_pointers")


def test_linear_membership_check_flagged_unannotated():
    source = """
def contains_pair(nums, target):
    for i in range(len(nums)):
        if target - nums[i] in nums:
            return True
    return False
"""
    # No annotation -> still list-like -> flagged.
    assert "linear_membership_check" in _types(source, "two_pointers")


def test_linear_membership_check_clean_set_annotation():
    source = """
from typing import List, Set

def two_sum(nums: List[int], target: int, seen: Set[int]) -> bool:
    for i in range(len(nums)):
        if target - nums[i] in seen:
            return True
    return False
"""
    # `seen` is a set -> O(1) lookup -> must NOT be flagged (false-positive fix).
    assert "linear_membership_check" not in _types(source, "two_pointers")


def test_linear_membership_check_clean_outside_loop():
    source = """
from typing import List

def has_target(nums: List[int], target: int) -> bool:
    return target in nums
"""
    assert "linear_membership_check" not in _types(source, "two_pointers")


# --------------------------------------------------------------------------- #
# extract_func_args + check_contract shape
# --------------------------------------------------------------------------- #

def test_extract_func_args_returns_name_annotation_pairs():
    from backend.checker import extract_func_args
    import ast

    tree = ast.parse(
        "def f(nums: list, target: int, flag):\n    return flag\n"
    )
    assert extract_func_args(tree) == [
        ("nums", "list"),
        ("target", "int"),
        ("flag", None),
    ]


def test_check_contract_result_shape():
    source = """
def f(arr):
    for i in range(len(arr)):
        for j in range(len(arr)):
            pass
"""
    result = check_contract(source, "sliding_window")
    assert result, "expected at least one violation"
    v = result[0]
    assert set(v.keys()) == {"type", "lineno", "severity", "label"}
    assert v["type"] == "nested_loop"
    assert v["severity"] == "major"
    assert v["label"] == "Nested loop detected — expected single pass"
    assert isinstance(v["lineno"], int)
