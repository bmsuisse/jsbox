import asyncio
import subprocess
import sys

import pytest

from jsbox import JavaScriptError, JsTimeoutError, Runtime


async def test_async_host_function_roundtrip():
    rt = Runtime()

    async def fetch(x):
        await asyncio.sleep(0.01)
        return x * 2

    rt.bind_function("fetch", fetch)
    assert await rt.eval_async("(async () => (await fetch(4)) + 1)()") == 9


async def test_non_promise_result_and_sync_function_work_in_eval_async():
    rt = Runtime()
    rt.bind_function("add", lambda a, b: a + b)
    assert await rt.eval_async("add(1, 2)") == 3


async def test_then_chaining_on_host_promise():
    rt = Runtime()

    async def f():
        return 5

    rt.bind_function("f", f)
    assert await rt.eval_async("f().then(x => x + 1)") == 6


async def test_calls_run_concurrently():
    rt = Runtime()
    live = peak = 0

    async def slow(x):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.05)
        live -= 1
        return x

    rt.bind_function("slow", slow)
    out = await rt.eval_async("Promise.all([1, 2, 3, 4].map(x => slow(x)))")
    assert out == [1, 2, 3, 4]
    assert peak == 4


async def test_host_exception_is_reraised_original():
    rt = Runtime()

    async def boom():
        raise KeyError("nope")

    rt.bind_function("boom", boom)
    with pytest.raises(KeyError, match="nope"):
        await rt.eval_async("(async () => { await boom() })()")


async def test_js_can_catch_async_host_error_and_cannot_read_message():
    rt = Runtime()

    async def boom():
        raise RuntimeError("secret /etc/passwd")

    rt.bind_function("boom", boom)
    out = await rt.eval_async("(async () => { try { await boom() } catch (e) { return e.message } })()")
    assert "secret" not in out and "RuntimeError" in out


async def test_timeout_covers_waiting_on_the_host():
    rt = Runtime()

    async def never():
        await asyncio.sleep(30)

    rt.bind_function("never", never)
    with pytest.raises(JsTimeoutError):
        await rt.eval_async("(async () => { await never() })()", timeout=0.2)
    assert await rt.eval_async("1 + 1", timeout=1) == 2


async def test_timeout_interrupts_a_busy_loop_in_eval_async():
    rt = Runtime()
    with pytest.raises(JsTimeoutError):
        await rt.eval_async("(async () => { for (;;) {} })()", timeout=0.1)


async def test_pending_tasks_are_cancelled_on_failure():
    rt = Runtime()
    cancelled = []

    async def hang():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    async def tick():
        await asyncio.sleep(0.05)

    rt.bind_function("hang", hang)
    rt.bind_function("tick", tick)
    with pytest.raises(JavaScriptError, match="boom"):
        await rt.eval_async("hang(); (async () => { await tick(); throw new Error('boom') })()")
    await asyncio.sleep(0.05)
    assert cancelled


async def test_runtime_is_reusable_after_async_runs():
    rt = Runtime()

    async def f():
        return 1

    rt.bind_function("f", f)
    for _ in range(20):
        assert await rt.eval_async("(async () => (await f()) + 1)()") == 2


async def test_promise_that_never_settles_is_an_error():
    rt = Runtime()
    with pytest.raises(JavaScriptError, match="never settled"):
        await rt.eval_async("new Promise(() => {})")


async def test_conversion_error_from_async_host_is_a_host_error():
    rt = Runtime()

    async def bad():
        return object()

    rt.bind_function("bad", bad)
    with pytest.raises(TypeError):
        await rt.eval_async("(async () => { await bad() })()")


def test_async_host_function_in_sync_eval_is_rejected_and_closed():
    rt = Runtime()

    async def f():
        return 1

    rt.bind_function("f", f)
    with pytest.raises(TypeError, match="eval_async"):
        rt.eval("f()")


def _survives(code):
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, (r.returncode, r.stderr[-300:])


def test_dropping_a_runtime_with_pending_async_calls_does_not_abort():
    _survives(
        """
import asyncio
from jsbox import Runtime

async def main():
    for _ in range(50):
        rt = Runtime()
        async def hang():
            await asyncio.sleep(30)
        rt.bind_function("hang", hang)
        try:
            await rt.eval_async("(async () => { await hang() })()", timeout=0.05)
        except Exception:
            pass
        del rt

asyncio.run(main())
"""
    )


def test_dropping_a_runtime_mid_async_without_cleanup_does_not_abort():
    _survives(
        """
import asyncio
from jsbox import Runtime

async def main():
    rt = Runtime()
    async def hang():
        await asyncio.sleep(30)
    rt.bind_function("hang", hang)
    rt._begin_async("hang(); hang(); 1", None)
    del rt

asyncio.run(main())
"""
    )
