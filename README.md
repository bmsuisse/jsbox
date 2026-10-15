<div align="center">

# jsbox

**A small JavaScript sandbox for Python**

`jsbox` embeds [QuickJS][quickjs] (via [rquickjs][rquickjs] and [PyO3][pyo3]) in Python.
Run untrusted, LLM-generated JavaScript with hard limits and nothing but the functions you hand it.

[![CI](https://github.com/bmsuisse/jsbox/actions/workflows/workflow.yml/badge.svg)][ci]
[![PyPI](https://img.shields.io/pypi/v/jsbox.svg)][pypi]
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

</div>

## What this is

Agent frameworks increasingly need to run **JavaScript the model itself wrote**: a data
transform, a "compute the answer" snippet, a batch of tool calls. That code is untrusted by
construction. `jsbox` runs it in an in-process QuickJS runtime that has no filesystem, no
network, no `require`, no timers and no Node.js APIs. The guest can reach exactly what the host
bound, and it is stopped by a wall-clock timeout, a heap limit and a stack limit.

It is small (a ~0.7 MB wheel, ~150 µs to create a runtime, no Rust toolchain needed to install)
and deliberately boring: an interpreter, not a JIT. If you need raw JavaScript speed, use a V8
based engine; if you need a tiny, fast-starting, hard-to-misuse sandbox, this is that.

```python
from jsbox import Runtime

rt = Runtime()
rt.bind_function("add", lambda a, b: a + b)
print(rt.eval("add(2, 3)"))  # 5
```

## Get started

```bash
pip install jsbox  # or: uv pip install jsbox
```

Python 3.10+ on Linux, macOS and Windows (one `abi3` wheel per platform).

### Tool calling with limits

```python
from jsbox import JsTimeoutError, Runtime

rt = Runtime(memory_limit=16 * 1024 * 1024)  # 16 MB heap


def get_weather(city: str) -> str:
    return f"72F and sunny in {city}"


rt.bind_function("getWeather", get_weather)

# Model-generated JS calling back into your tool:
print(rt.eval("getWeather('Zurich')"))  # 72F and sunny in Zurich

# Code that might not terminate is stopped, and the runtime stays usable:
try:
    rt.eval("while (true) {}", timeout=2.0)
except JsTimeoutError:
    print("runaway script killed after 2s")
print(rt.eval("1 + 1"))  # 2
```

### Data in, data out

Values are copied across the boundary; the guest never holds a reference to a Python object.

| JavaScript | Python |
|---|---|
| `null`, `undefined` | `None` |
| `boolean`, `string` | `bool`, `str` |
| integral `number`, `BigInt` | `int` (BigInt never wraps) |
| other `number` | `float` |
| `Array` | `list` (a `tuple` goes in as an array) |
| plain `Object` | `dict` (own enumerable keys, functions skipped) |
| `Uint8Array`, `ArrayBuffer` | `bytes` (`bytes`/`bytearray` go in as `Uint8Array`) |

```python
rt.bind_object("config", {"debug": True, "tags": ["a", "b"], "blob": b"\x00\x01"})
print(rt.eval("config.tags.length"))  # 2
print(rt.eval("({ok: true, rows: [1, 2, 3]})"))  # {'ok': True, 'rows': [1, 2, 3]}
```

Anything else (functions, symbols, `Date`, non-`str` dict keys, cycles) raises instead of being
silently mangled.

### Promises

A script that returns a promise is settled by draining microtasks, so `async`/`await` works.
A promise that can never settle (there are no timers or I/O) is an error, not a hang.

```python
print(rt.eval("(async () => { await null; return 7 })()"))  # 7
```

### ES modules

```python
rt.add_static_module("math", "export const answer = 42;")
rt.add_static_module("app", "import {answer} from 'math'; export const x = answer + 1;")
print(rt.eval_module("app"))  # {'x': 43}
```

Only modules you registered can be imported, by `import` or `import()`. There is no file or URL
loading.

### Errors

| Situation | Python exception |
|---|---|
| JS throws, or a syntax error | `JavaScriptError` (message and JS stack) |
| `timeout=` exceeded | `JsTimeoutError` |
| A bound Python function raises | **the original exception**, re-raised unchanged |
| Value cannot be converted | `TypeError` / `ValueError` |

JS that catches a host error sees only `host function failed: <ExceptionClass> [#n]`. The
message of your Python exception (paths, secrets, internals) never reaches the guest.

## Security model

There is no permission system to configure. A fresh `Runtime` can compute and nothing else; what
it can *do* is exactly what you bound.

- **No ambient authority.** No `require`, `process`, `fetch`, `setTimeout`, file or network access.
- **Heap, stack and copy limits with safe defaults.** `memory_limit` (64 MB), `max_stack_size`
  (1 MB), `max_value_bytes` (16 MB per conversion). Pass `0` to `memory_limit` or
  `max_stack_size` to disable them.
- **Timeouts you cannot swallow.** `timeout=` interrupts the interpreter loop; `try/catch` in the
  guest cannot catch it, and it also covers property getters hit while converting the result.
  **It cannot interrupt a single long native operation**: a catastrophic regular expression
  (`/^(a+)+$/.test('a'.repeat(40) + 'b')`) runs to completion, and some builtins (huge `BigInt`
  powers) overshoot the deadline. See below.
- **Host errors are not guest-readable**, and a swallowed host error is never blamed on a later,
  unrelated JS error.
- **Isolated runtimes.** Two `Runtime`s share nothing; a `Runtime` is bound to its thread.

What this is **not**: QuickJS is a C engine in your process. A memory-safety bug in it is a bug
in your process, so for hostile multi-tenant workloads also use OS-level isolation (a container
or a separate process). Bound host functions are privileged code you wrote, so keep them narrow.
A timeout cannot interrupt a Python callback that is itself stuck (it fires as soon as that
callback returns), nor a runaway regular expression: **for untrusted code, run the `Runtime` in
a process you can kill** (or reject patterns up front). A built-in hard kill is the open item.

## Limitations

- Host functions are synchronous; `async def` callables raise a clear `TypeError`.
- No timers, `fetch`, `Date`/`Map`/`Set` conversion, or `console` routing yet.
- Interpreter speed: roughly 10x slower than V8 on CPU-bound code (`fib(30)` ≈ 90 ms), and a
  complete host call (JS to Python and back) costs ≈ 0.5 µs.
- Python ints outside `i64` cannot be sent in.

## Development

```bash
python -m venv .venv && .venv/bin/pip install maturin pytest hypothesis
.venv/bin/maturin develop --release
.venv/bin/pytest
```

[quickjs]: https://bellard.org/quickjs/
[rquickjs]: https://github.com/DelSkayn/rquickjs
[pyo3]: https://pyo3.rs/
[pypi]: https://pypi.org/project/jsbox/
[ci]: https://github.com/bmsuisse/jsbox/actions/workflows/workflow.yml
