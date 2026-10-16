"""Basic usage: evaluate JS, keep state between calls, handle errors."""

from jsbox import JavaScriptError, Runtime

rt = Runtime()
print("2 + 2 =", rt.eval("2 + 2"))
print("Math.sqrt(25) =", rt.eval("Math.sqrt(25)"))

# State persists across evals on the same Runtime.
rt.eval("globalThis.counter = 0")
for _ in range(3):
    print("counter ->", rt.eval("++counter"))

# async/await works: the returned promise is settled for you.
print("async ->", rt.eval("(async () => { await null; return 7 })()"))

# JS errors become JavaScriptError with name / message / stack.
try:
    rt.eval("throw new TypeError('nope')")
except JavaScriptError as e:
    print("caught:", e.name, e.message)

# Runtimes are isolated from each other.
print("other runtime sees counter:", Runtime().eval("typeof counter"))
