import time

import pytest

from jsbox import JavaScriptError, JsTimeoutError, Runtime


def test_timeout_on_infinite_loop_and_runtime_survives(rt):
    t = time.perf_counter()
    with pytest.raises(JsTimeoutError):
        rt.eval("while (true) {}", timeout=0.1)
    assert time.perf_counter() - t < 1.0
    assert rt.eval("1 + 1", timeout=1) == 2


def test_timeout_is_per_eval_not_cumulative(rt):
    for _ in range(5):
        assert rt.eval("(() => { let s = 0; for (let i = 0; i < 1e5; i++) s += i; return s })()", timeout=5) > 0


@pytest.mark.parametrize(
    "src",
    [
        "for (;;) {}",
        "(function f() { for (;;) {} })()",
        "[...Array(1e9).keys()]",
        "'x'.repeat(1e9).split('').length",
        "(async () => { for (;;) { await null } })()",
        "try { while (true) {} } catch (e) {}",
        "try { while (true) {} } finally {}",
    ],
)
def test_timeout_every_shape(src):
    rt = Runtime(memory_limit=256 * 1024 * 1024)
    with pytest.raises((JsTimeoutError, JavaScriptError)):
        rt.eval(src, timeout=0.2)
    assert rt.eval("2", timeout=1) == 2


def test_guest_cannot_swallow_the_timeout(rt):
    with pytest.raises(JsTimeoutError):
        rt.eval("while (true) { try { for (;;) {} } catch (e) {} }", timeout=0.1)


@pytest.mark.parametrize("bad", [-1.0, float("nan"), float("inf"), -float("inf")])
def test_invalid_timeout_is_a_value_error_not_a_panic(rt, bad):
    with pytest.raises(ValueError):
        rt.eval("1", timeout=bad)


def test_zero_timeout_does_not_panic(rt):
    try:
        rt.eval("1", timeout=0)
    except JsTimeoutError:
        pass


def test_huge_timeout_does_not_panic(rt):
    assert rt.eval("1", timeout=1e30) == 1


def test_default_memory_limit_stops_allocation_bomb(rt):
    with pytest.raises(JavaScriptError, match="memory"):
        rt.eval("const a = []; for (;;) a.push(new Array(1e5).fill(1))")
    assert rt.eval("1") == 1


def test_explicit_memory_limit():
    rt = Runtime(memory_limit=4 * 1024 * 1024)
    with pytest.raises(JavaScriptError, match="memory"):
        rt.eval("'x'.repeat(64 * 1024 * 1024)")


def test_string_doubling_bomb():
    rt = Runtime(memory_limit=8 * 1024 * 1024)
    with pytest.raises(JavaScriptError):
        rt.eval("let s = 'x'; for (;;) s += s")


def test_stack_overflow_is_a_catchable_error(rt):
    with pytest.raises(JavaScriptError, match="stack|recursion"):
        rt.eval("function f() { return f() + 1 } f()")
    assert rt.eval("1") == 1


def test_small_stack_limit():
    rt = Runtime(max_stack_size=64 * 1024)
    with pytest.raises(JavaScriptError):
        rt.eval("function f(n) { return n ? f(n - 1) + 1 : 0 } f(1e5)")


def test_max_value_bytes_blocks_big_inputs():
    rt = Runtime(max_value_bytes=1024)
    with pytest.raises(ValueError, match="max_value_bytes"):
        rt.bind_object("big", "x" * 4096)
    with pytest.raises(ValueError, match="max_value_bytes"):
        rt.bind_object("big", b"x" * 4096)
    with pytest.raises(ValueError, match="max_value_bytes"):
        rt.bind_object("big", list(range(10_000)))


def test_max_value_bytes_blocks_big_outputs():
    rt = Runtime(max_value_bytes=1024)
    with pytest.raises(ValueError, match="max_value_bytes"):
        rt.eval("'x'.repeat(10000)")
    with pytest.raises(ValueError, match="max_value_bytes"):
        rt.eval("new Array(10000).fill(0)")


def test_max_value_bytes_applies_to_host_calls():
    rt = Runtime(max_value_bytes=1024)
    rt.bind_function("sink", lambda x: len(x))
    with pytest.raises(ValueError, match="max_value_bytes"):
        rt.eval("sink('x'.repeat(10000))")


def test_value_within_budget_is_fine():
    rt = Runtime(max_value_bytes=4096)
    rt.bind_object("s", "x" * 1000)
    assert rt.eval("s.length") == 1000


def test_promise_that_never_settles_is_an_error_not_a_hang(rt):
    with pytest.raises(JavaScriptError, match="never settled"):
        rt.eval("new Promise(() => {})")
