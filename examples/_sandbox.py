"""Shared helpers for the Monty + jsbox examples.

`new_runtime()` gives a jsbox Runtime with the browser-basics polyfills (virtual-time timers,
self/global, Blob, ...) already loaded; `load_libs(rt, *names)` evaluates vendored bundles from
``vendor/libs`` into it. jsbox itself needs neither on a bare runtime: they exist because
libraries such as vega or d3 probe for ``self``/``setTimeout``.
"""

import pathlib

from _polyfills import WEB_POLYFILLS
from jsbox import Runtime

LIBS = pathlib.Path(__file__).resolve().parent.parent / "vendor" / "libs"


def new_runtime(memory_mb: int = 256, **kw) -> Runtime:
    rt = Runtime(memory_limit=memory_mb * 1024 * 1024, **kw)
    rt.eval(WEB_POLYFILLS + "\n;0")
    return rt


def load_libs(rt: Runtime, *names: str) -> None:
    rt.eval("\n;\n".join((LIBS / n).read_text() for n in names) + "\n;0", timeout=60)
