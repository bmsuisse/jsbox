"""Expose Python functions and data to JavaScript, sync and async."""

import asyncio

from jsbox import Runtime

rt = Runtime()
rt.bind_function("add", lambda a, b: a + b)
rt.bind_function("greet", lambda name, punct="!": f"Hello, {name}{punct}")
rt.bind_object("config", {"debug": True, "tags": ["a", "b"], "blob": b"\x00\x01"})

print(rt.eval("add(2, 3)"))
print(rt.eval("greet('Zurich')"))
print(rt.eval("config.tags.length"), rt.eval("config.blob instanceof Uint8Array"))

# Python exceptions propagate unchanged; JS can catch them but only sees a generic message.
def boom():
    raise ValueError("secret internal detail")


rt.bind_function("boom", boom)
print(rt.eval("try { boom() } catch (e) { e.message }"))
try:
    rt.eval("boom()")
except ValueError as e:
    print("host got original exception:", e)


# Async host functions: use eval_async; calls run concurrently via Promise.all.
async def fetch(x):
    await asyncio.sleep(0.05)
    return x * 2


async def main():
    art = Runtime()
    art.bind_function("fetch", fetch)
    print(await art.eval_async("Promise.all([1, 2, 3].map(x => fetch(x)))", timeout=5))


asyncio.run(main())
