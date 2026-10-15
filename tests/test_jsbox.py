import pytest

from jsbox import JavaScriptError, JsTimeoutError, Runtime


@pytest.fixture
def rt():
    return Runtime(memory_limit=32 * 1024 * 1024)


def test_eval_basics(rt):
    assert rt.eval("2 + 2") == 4
    assert rt.eval("1.5") == 1.5
    assert rt.eval("'hi'") == "hi"
    assert rt.eval("null") is None
    assert rt.eval("undefined") is None
    assert rt.eval("[1, 'a', {b: true}]") == [1, "a", {"b": True}]
    assert rt.eval("1e10") == 10_000_000_000
    assert rt.eval("2n**60n") == 2**60


def test_bytes_roundtrip(rt):
    rt.bind_object("data", b"\x00\x01\xff")
    assert rt.eval("data instanceof Uint8Array && data.length") == 3
    assert rt.eval("data") == b"\x00\x01\xff"


def test_bind_function_and_object(rt):
    rt.bind_function("add", lambda a, b: a + b)
    rt.bind_object("cfg", {"v": "1.0", "xs": [1, 2]})
    assert rt.eval("add(2, 3)") == 5
    assert rt.eval("cfg.v + cfg.xs.length") == "1.02"


def test_host_exception_propagates(rt):
    def boom():
        raise KeyError("nope")

    rt.bind_function("boom", boom)
    with pytest.raises(KeyError):
        rt.eval("boom()")


def test_js_error(rt):
    with pytest.raises(JavaScriptError, match="bad"):
        rt.eval("throw new Error('bad')")
    with pytest.raises(JavaScriptError):
        rt.eval("let x = ;")


def test_timeout_and_survival(rt):
    with pytest.raises(JsTimeoutError):
        rt.eval("while (true) {}", timeout=0.1)
    assert rt.eval("1 + 1", timeout=1) == 2


def test_memory_limit(rt):
    with pytest.raises(JavaScriptError, match="memory"):
        rt.eval("const a = []; for (;;) a.push(new Array(1e5).fill(1))")


def test_promise_settles(rt):
    assert rt.eval("(async () => { await null; return 7 })()") == 7
    with pytest.raises(JavaScriptError, match="no"):
        rt.eval("(async () => { throw new Error('no') })()")


def test_modules(rt):
    rt.add_static_module("math", "export const answer = 42;")
    rt.add_static_module("app", "import {answer} from 'math'; export const x = answer + 1;")
    assert rt.eval_module("app")["x"] == 43
    with pytest.raises(JavaScriptError):
        rt.eval_module("missing")


def test_async_host_function_rejected(rt):
    async def f():
        return 1

    rt.bind_function("f", f)
    with pytest.raises(TypeError):
        rt.eval("f()")
