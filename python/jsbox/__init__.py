import asyncio

from jsbox import _jsbox
from jsbox._jsbox import JavaScriptError, JsTimeoutError


class Runtime(_jsbox.Runtime):
    __doc__ = _jsbox.Runtime.__doc__

    async def eval_async(self, code, timeout=None):
        """Evaluate a script whose host functions may be ``async def``.

        Awaitables returned by bound functions run on the caller's event loop
        and reach JS as Promises. ``timeout`` covers the whole call, including
        time spent awaiting the host.
        """
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout
        self._begin_async(code, timeout)
        try:
            while True:
                done, result = self._poll()
                if done:
                    return result
                wait = None if deadline is None else max(deadline - loop.time(), 0)
                tasks = {task: id_ for id_, task in result}
                finished, _ = await asyncio.wait(tasks, timeout=wait, return_when=asyncio.FIRST_COMPLETED)
                if not finished:
                    raise JsTimeoutError("script exceeded its timeout")
                for task in finished:
                    exc = task.exception() if not task.cancelled() else asyncio.CancelledError()
                    if exc is None:
                        self._settle(tasks[task], True, task.result())
                    else:
                        self._settle(tasks[task], False, exc)
        finally:
            self._end_async()


__all__ = ["JavaScriptError", "JsTimeoutError", "Runtime"]
