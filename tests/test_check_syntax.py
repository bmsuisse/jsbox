import pytest

from jsbox import JavaScriptError, Runtime


def test_valid_and_invalid():
    rt = Runtime()
    rt.check_syntax("const x = 1; x + 1")
    rt.check_syntax("export const a = 1;", module=True)
    with pytest.raises(JavaScriptError) as e:
        rt.check_syntax("const = ;")
    assert e.value.name == "SyntaxError"
    with pytest.raises(JavaScriptError):
        rt.check_syntax("export const = 1", module=True)


def test_does_not_execute():
    rt = Runtime()
    rt.check_syntax("globalThis.ran = true")
    assert rt.eval("typeof ran") is None or rt.eval("typeof ran") == "undefined"
