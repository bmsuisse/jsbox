# jsbox examples

Run from the repo root after `maturin develop`: `python examples/<name>.py`.

- `basic_eval.py` – eval, persistent state, promises, errors
- `bindings.py` – bind Python functions/objects, error propagation, async host functions
- `type_conversion.py` – value mapping both ways, strict vs `lossy=True`
- `resource_limits.py` – timeout, heap, stack and value-size limits; no ambient authority
- `modules_basic.py` – static ES modules and dynamic `import()`
- `agent_tool_calling.py` – model-written JS fanning out to async tools, with `console` capture
- `threading_gil.py` – the GIL is released while JS runs
