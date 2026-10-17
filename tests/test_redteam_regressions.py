"""Regressions for bugs found by the red-team pass. Each test failed before its fix."""

import asyncio
import subprocess
import sys

import pytest

from jsbox import JavaScriptError, JsTimeoutError, Runtime


def test_stack_limit_beyond_native_stack_is_rejected():
    for bad in (0, 16 * 1024 * 1024):
        with pytest.raises(ValueError):
            Runtime(max_stack_size=bad)


def test_deep_recursion_raises_instead_of_crashing():
    code = "from jsbox import Runtime; Runtime().eval('function f(n){return f(n+1)+1} f(0)')"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 1 and "Maximum call stack" in r.stderr  # a JavaScriptError, not a segfault


def test_reentrant_eval_is_a_clean_error_not_a_panic():
    rt = Runtime()
    rt.bind_function("again", lambda: rt.eval("1"))
    with pytest.raises(RuntimeError, match="busy"):
        rt.eval("again()")
    assert rt.eval("1 + 1") == 2  # still usable


def test_huge_array_length_does_not_panic():
    with pytest.raises(ValueError):
        Runtime().eval("const a = [1]; a.length = 2147483648; a")


def test_value_budget_is_per_call_not_per_argument():
    rt = Runtime(max_value_bytes=1024 * 1024)
    rt.bind_function("take", lambda *a: len(a))
    with pytest.raises(ValueError):
        rt.eval("const s = 'x'.repeat(200000); take(...Array(100).fill(s))")


@pytest.mark.parametrize("exc", [KeyboardInterrupt, SystemExit])
def test_host_base_exception_cannot_be_swallowed_by_guest(exc):
    rt = Runtime()

    def boom():
        raise exc()

    rt.bind_function("boom", boom)
    with pytest.raises(exc):
        rt.eval("let n = 0; while (true) { try { boom() } catch (e) { n++ } }")
    assert rt.eval("1 + 1") == 2


def test_lossy_does_not_swallow_timeout():
    rt = Runtime(lossy=True)
    with pytest.raises(JsTimeoutError):
        rt.eval("WeakSet.prototype.has = function () { while (true) {} }; ({a: new Set([1])})", timeout=0.5)


def test_thrown_primitives_are_readable_and_leak_nothing():
    rt = Runtime()
    for js, text in [("throw 'boom'", "boom"), ("throw 42", "42"), ("throw null", "null"), ("throw {a: 1}", "[object]")]:
        with pytest.raises(JavaScriptError) as e:
            rt.eval(js)
        assert e.value.message == text and "0x" not in str(e.value)


def test_overlapping_eval_async_is_rejected_not_cross_wired():
    async def h(tag, d):
        await asyncio.sleep(d)
        return tag

    async def main():
        rt = Runtime()
        rt.bind_function("h", h)
        a = asyncio.ensure_future(rt.eval_async("h('A', 0.2).then(x => 'A:' + x)"))
        await asyncio.sleep(0.02)
        with pytest.raises(RuntimeError, match="busy"):
            await rt.eval_async("h('B', 0.01).then(x => 'B:' + x)")
        assert await a == "A:A"  # the first call is unharmed

    asyncio.run(main())


def test_sync_eval_during_eval_async_is_rejected():
    async def h():
        await asyncio.sleep(0.1)
        return 1

    async def main():
        rt = Runtime()
        rt.bind_function("h", h)
        a = asyncio.ensure_future(rt.eval_async("h()"))
        await asyncio.sleep(0.02)
        with pytest.raises(RuntimeError, match="busy"):
            rt.eval("1")
        assert await a == 1

    asyncio.run(main())
