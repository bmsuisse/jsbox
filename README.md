# jsbox

Embedded JavaScript for Python, powered by [QuickJS](https://bellard.org/quickjs/) via [rquickjs](https://github.com/DelSkayn/rquickjs) and Rust.

```python
from jsbox import Runtime

rt = Runtime(memory_limit=32 * 1024 * 1024)
rt.bind_function("add", lambda a, b: a + b)
assert rt.eval("add(2, 3)") == 5
rt.eval("while (true) {}", timeout=0.1)  # raises JsTimeoutError
```

Status: early. Sync host functions, ES modules, timeouts, memory limits.
