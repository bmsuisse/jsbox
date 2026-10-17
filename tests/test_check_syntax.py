import pytest

from jsbox import JavaScriptError, Runtime


def test_valid_and_invalid():
    rt = Runtime()
    rt.check_syntax("const x = 1; x + 1")
    rt.check_syntax("export const a = 1;", module=True)
    rt.check_syntax("import {x} from 'not-registered'; export {x}", module=True)  # imports not resolved
    with pytest.raises(JavaScriptError) as e:
        rt.check_syntax("const = ;")
    assert e.value.name == "SyntaxError"
    with pytest.raises(JavaScriptError):
        rt.check_syntax("export const = 1", module=True)


def test_does_not_execute():
    rt = Runtime()
    rt.check_syntax("globalThis.ran = true")
    assert rt.eval("typeof ran") == "undefined"


@pytest.mark.parametrize("src", ["}); throw 'SIDE'; (function(){", "}); while(true){}; (function(){"])
def test_wrapper_breakout_is_a_syntax_error_not_execution(src):
    # A Function()-constructor based check would run the middle statement.
    with pytest.raises(JavaScriptError) as e:
        Runtime().check_syntax(src)
    assert e.value.name == "SyntaxError" and "SIDE" not in str(e.value)


def test_guest_cannot_hijack_check():
    rt = Runtime()
    rt.eval("globalThis.hit = 0; globalThis.Function = function () { hit++ }; 0")
    with pytest.raises(JavaScriptError):
        rt.check_syntax("const = ;")
    assert rt.eval("hit") == 0
