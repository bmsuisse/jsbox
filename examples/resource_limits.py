"""Run untrusted code safely: timeouts, heap, stack and value-size limits."""

from jsbox import JavaScriptError, JsTimeoutError, Runtime

rt = Runtime(memory_limit=16 * 1024 * 1024, max_stack_size=256 * 1024)


def attempt(label, code, **kw):
    try:
        print(f"{label}: {rt.eval(code, **kw)}")
    except JsTimeoutError:
        print(f"{label}: timed out")
    except JavaScriptError as e:
        print(f"{label}: {e.name}: {e.message}")


attempt("infinite loop", "while (true) {}", timeout=0.5)
# try/catch in the guest cannot swallow a timeout
attempt("swallow attempt", "try { while (true) {} } catch (e) {} 'escaped'", timeout=0.5)
attempt("heap bomb", "const a = []; while (true) a.push('x'.repeat(1e6))")
attempt("deep recursion", "(function f() { return f() + 1 })()")
attempt("ambient authority", "[typeof require, typeof process, typeof fetch, typeof setTimeout].join()")

# The runtime survives every one of those.
print("still alive:", rt.eval("1 + 1"))

big = Runtime(max_value_bytes=1024)
try:
    big.eval("'x'.repeat(100000)")
except (TypeError, ValueError) as e:
    print("oversized result rejected:", type(e).__name__)
