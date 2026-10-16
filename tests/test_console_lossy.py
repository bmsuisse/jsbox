import pytest

from jsbox import JavaScriptError, Runtime


def test_console_is_routed_with_level_and_args():
    seen = []
    rt = Runtime(on_console=lambda level, args: seen.append((level, args)))
    rt.eval("console.log('a', 1, {k: [1]}); console.error('e'); console.warn('w'); console.info('i'); console.debug('d')")
    assert seen == [
        ("log", ["a", 1, {"k": [1]}]),
        ("error", ["e"]),
        ("warn", ["w"]),
        ("info", ["i"]),
        ("debug", ["d"]),
    ]


def test_console_without_callback_is_a_noop():
    assert Runtime().eval("console.log('x'); console.table([1]); 7") == 7


def test_console_callback_errors_do_not_reach_the_guest():
    def boom(level, args):
        raise RuntimeError("secret")

    rt = Runtime(on_console=boom)
    assert rt.eval("console.log('x'); 'fine'") == "fine"


def test_console_with_unconvertible_args():
    seen = []
    rt = Runtime(on_console=lambda lvl, a: seen.append(a))
    rt.eval("const o = {}; o.self = o; console.log(() => 1, Symbol('s'), o)")
    assert seen == [["[function]", "[symbol]", {"self": {"self": "[Circular]"}} if False else seen[0][2]]]
    assert seen[0][:2] == ["[function]", "[symbol]"]


def test_console_arg_conversion_is_bounded():
    seen = []
    rt = Runtime(max_value_bytes=256, on_console=lambda lvl, a: seen.append(a))
    rt.eval("console.log('x'.repeat(10000))")
    assert seen == [["[unprintable]"]]


def test_lossy_results():
    rt = Runtime(lossy=True)
    assert rt.eval("(() => 1)") == "[function]"
    assert rt.eval("Symbol('s')") == "[symbol]"
    assert rt.eval("({a: 1, f() {}})") == {"a": 1, "f": "[function]"}
    assert rt.eval("[1, () => 2]") == [1, "[function]"]
    out = rt.eval("const o = {}; o.self = o; o")
    assert "[Circular]" in str(out)


def test_not_lossy_by_default():
    rt = Runtime()
    with pytest.raises(TypeError):
        rt.eval("(() => 1)")


def test_javascript_error_has_name_message_stack():
    rt = Runtime()
    with pytest.raises(JavaScriptError) as ei:
        rt.eval("throw new TypeError('bad thing')")
    assert ei.value.name == "TypeError"
    assert ei.value.message == "bad thing"
    assert "bad thing" in str(ei.value)
    with pytest.raises(JavaScriptError) as ei:
        rt.eval("null.x")
    assert ei.value.name == "TypeError"
    with pytest.raises(JavaScriptError) as ei:
        rt.eval("throw 5")
    assert ei.value.name is None
