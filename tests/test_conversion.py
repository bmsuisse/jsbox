import math

import pytest

from jsbox import JavaScriptError


@pytest.mark.parametrize(
    "src, expected",
    [
        ("null", None),
        ("undefined", None),
        ("true", True),
        ("false", False),
        ("0", 0),
        ("-7", -7),
        ("1.5", 1.5),
        ("1e10", 10_000_000_000),
        ("2**53 - 1", 2**53 - 1),
        ("'text'", "text"),
        ("''", ""),
        ("[]", []),
        ("({})", {}),
        ("[1, [2, [3]]]", [1, [2, [3]]]),
        ("({a: {b: [1, 'x', null]}})", {"a": {"b": [1, "x", None]}}),
        ("2n ** 60n", 2**60),
        ("-(2n ** 60n)", -(2**60)),
    ],
)
def test_js_to_python(rt, src, expected):
    result = rt.eval(src)
    assert result == expected
    assert type(result) is type(expected)


def test_special_floats(rt):
    assert math.isnan(rt.eval("NaN"))
    assert rt.eval("Infinity") == math.inf
    assert rt.eval("-Infinity") == -math.inf


@pytest.mark.parametrize(
    "value",
    [None, True, False, 0, -1, 42, 2**53, 2**62, -(2**62), 1.25, "", "x", [], {}, [1, "a", None], {"k": [1, {"z": 2}]}],
)
def test_python_roundtrip(rt, value):
    rt.bind_object("v", value)
    assert rt.eval("v") == value


def test_tuple_becomes_array(rt):
    rt.bind_object("t", (1, 2, 3))
    assert rt.eval("Array.isArray(t) && t.length") == 3


def test_bytes_roundtrip(rt):
    data = bytes(range(256))
    rt.bind_object("b", data)
    assert rt.eval("b instanceof Uint8Array") is True
    assert rt.eval("b") == data
    assert rt.eval("b.reduce((a, x) => a + x, 0)") == sum(data)


def test_bytearray_and_empty_bytes(rt):
    rt.bind_object("b", bytearray(b"ab"))
    rt.bind_object("e", b"")
    assert rt.eval("b") == b"ab"
    assert rt.eval("e") == b""


def test_array_buffer_and_typed_array_return_bytes(rt):
    assert rt.eval("new Uint8Array([1, 2, 3])") == b"\x01\x02\x03"
    assert rt.eval("new Uint8Array([1, 2, 3]).buffer") == b"\x01\x02\x03"


@pytest.mark.parametrize("bad", [object(), {1, 2}, lambda: 1, 1 + 2j, {1: "int key"}])
def test_unconvertible_python_values(rt, bad):
    with pytest.raises((TypeError, ValueError)):
        rt.bind_object("x", bad)


def test_int_out_of_i64_range(rt):
    with pytest.raises(ValueError):
        rt.bind_object("x", 2**70)


@pytest.mark.parametrize("n", [2**63, 2**64, 2**80, -(2**63) - 1, 10**30, -(10**30)])
def test_bigint_never_wraps(rt, n):
    assert rt.eval(f"{n}n") == n


def test_bigint_too_large_for_python_digit_limit(rt):
    with pytest.raises(ValueError):
        rt.eval("10n ** 5000n")


@pytest.mark.parametrize("src", ["Symbol('s')", "(() => 1)", "(class A {})"])
def test_unconvertible_js_values(rt, src):
    with pytest.raises(TypeError):
        rt.eval(src)


def test_functions_inside_objects_are_skipped(rt):
    assert rt.eval("({a: 1, f() { return 2 }})") == {"a": 1}


def test_cyclic_structure_is_rejected_not_hung(rt):
    with pytest.raises(ValueError, match="deeply"):
        rt.eval("const o = {}; o.self = o; o")


def test_deeply_nested_python_value_is_rejected(rt):
    v = []
    for _ in range(200):
        v = [v]
    with pytest.raises(ValueError, match="deeply"):
        rt.bind_object("v", v)


def test_python_self_referencing_list_is_rejected(rt):
    v = []
    v.append(v)
    with pytest.raises(ValueError):
        rt.bind_object("v", v)


def test_lone_surrogate_does_not_crash(rt):
    try:
        rt.eval("'\\ud800'")
    except (ValueError, JavaScriptError):
        pass
    assert rt.eval("1") == 1


@pytest.mark.parametrize("key", ["", "\x00", "a\x00b", "\x00\x00", "é", "\u2028", "\U0001F389"])
def test_odd_dict_keys_survive(rt, key):
    rt.bind_object("v", {key: 1})
    assert rt.eval("v") == {key: 1}
