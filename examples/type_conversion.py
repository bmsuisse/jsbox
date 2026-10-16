"""Reference for how values cross the Python <-> JavaScript boundary."""

from jsbox import JavaScriptError, Runtime

rt = Runtime()

print("JS -> Python")
for js in [
    "null", "undefined", "true", "'text'", "42", "3.5", "2n ** 100n",
    "[1, 'a', [2]]", "({a: 1, b: {c: [true]}})", "new Uint8Array([1, 2, 3])",
]:
    value = rt.eval(js)
    print(f"  {js:32} -> {value!r} ({type(value).__name__})")

print("\nPython -> JS")
for name, value in [
    ("none", None), ("flag", False), ("n", 7), ("f", 1.5), ("s", "héllo"),
    ("lst", [1, 2]), ("tup", (1, 2)), ("d", {"k": [1]}), ("b", b"abc"),
]:
    rt.bind_object(name, value)
    print(f"  {name:5} {value!r:14} -> {rt.eval(f'Array.isArray({name}) ? "array" : typeof ' + name)}")

print("\nUnconvertible results")
for js in ["(() => 1)", "Symbol('x')", "(() => { const a = {}; a.self = a; return a })()"]:
    try:
        rt.eval(js)
    except (TypeError, ValueError, JavaScriptError) as e:
        print(f"  strict: {type(e).__name__}")

lossy = Runtime(lossy=True)
print("  lossy:", lossy.eval("({ok: true, run() {}, s: Symbol('x'), d: new Date(0), m: new Map([[1, 2]])})"))
