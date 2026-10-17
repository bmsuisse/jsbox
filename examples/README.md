# jsbox examples

Run from the repo root after `maturin develop`: `python examples/<name>.py`.

- `basic_eval.py` – eval, persistent state, promises, errors
- `bindings.py` – bind Python functions/objects, error propagation, async host functions
- `type_conversion.py` – value mapping both ways, strict vs `lossy=True`
- `resource_limits.py` – timeout, heap, stack and value-size limits; no ambient authority
- `modules_basic.py` – static ES modules and dynamic `import()`
- `agent_tool_calling.py` – model-written JS fanning out to async tools, with `console` capture
- `threading_gil.py` – the GIL is released while JS runs

## Monty + jsbox

The model's Python runs in [Monty](https://github.com/pydantic/monty) (`pip install pydantic-monty`),
its JavaScript in jsbox, both calling the same host tools. Vendored bundles live in `vendor/libs`
(`_sandbox.py` loads them; `_polyfills.py` adds virtual-time timers and friends).

- `monty_and_jsbox.py` – shared tool budget, Vega-Lite chart (`sales.svg`)
- `monty_sql_charts.py` – SQLite question answered in Monty, Vega-Lite SVGs
- `monty_echarts_dashboard.py` – anomaly detection, ECharts `dashboard.html`
- `monty_d3_network.py` – dependency graph, d3-force/delaunay SVG (~5-10 s: interpreter, no JIT)
- `monty_turf_geo.py` – fleet geometry with turf, SVG map
- `monty_taxi_flows.py` – 60k NYC taxi trips to a three.js `.glb` (Arrow tables built in JS)
- `monty_three_city.py`, `monty_three_julia.py`, `monty_three_orbits.py`, `monty_three_terrain.py` – three.js scenes exported as `.glb`
- `monty_spreadsheet_deck.py` – pptxgenjs `.pptx` deck (`Runtime(buffer=True)`)
