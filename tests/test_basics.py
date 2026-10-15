import pytest

from jsbox import JavaScriptError, Runtime


def test_arithmetic_and_strings(rt):
    assert rt.eval("2 + 2") == 4
    assert rt.eval("'a' + 'b'") == "ab"
    assert rt.eval("[1,2,3].map(x => x * 2)") == [2, 4, 6]


def test_state_persists_across_evals(rt):
    rt.eval("globalThis.counter = 1")
    rt.eval("counter += 1")
    assert rt.eval("counter") == 2


def test_runtimes_are_isolated():
    a, b = Runtime(), Runtime()
    a.eval("globalThis.secret = 'a'")
    assert b.eval("typeof secret") == "undefined"


def test_many_runtimes_and_evals():
    for i in range(200):
        assert Runtime().eval(f"{i} + 1") == i + 1


def test_syntax_error_is_catchable_and_runtime_survives(rt):
    with pytest.raises(JavaScriptError):
        rt.eval("let x = ;")
    assert rt.eval("1") == 1


def test_thrown_error_carries_message_and_stack(rt):
    with pytest.raises(JavaScriptError, match="boom") as ei:
        rt.eval("function f() { throw new Error('boom') } f()")
    assert "f" in str(ei.value)


@pytest.mark.parametrize("src", ["throw 1", "throw 'x'", "throw null", "throw {a: 1}"])
def test_throwing_non_errors(rt, src):
    with pytest.raises(JavaScriptError):
        rt.eval(src)


def test_unicode_roundtrip(rt):
    s = "héllo ✓ 日本語 🎉"
    rt.bind_object("s", s)
    assert rt.eval("s") == s
    assert rt.eval("s.length") == len(s.encode("utf-16-le")) // 2


def test_empty_script_is_none(rt):
    assert rt.eval("") is None
