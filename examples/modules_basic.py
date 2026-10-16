"""ES modules: only modules you register can be imported."""

from jsbox import JavaScriptError, Runtime

rt = Runtime()
rt.add_static_module("math", "export const answer = 42; export const double = x => x * 2;")
rt.add_static_module("app", "import {answer, double} from 'math'; export const x = double(answer) + 1;")
print(rt.eval_module("app"))  # {'x': 85}

# Dynamic import works for registered modules too.
print(rt.eval("import('math').then(m => m.answer)"))

# Unregistered specifiers (files, URLs, node builtins) are rejected.
for spec in ["./local.js", "https://example.com/x.js", "fs"]:
    try:
        rt.eval(f"import({spec!r})")
    except JavaScriptError as e:
        print(f"import({spec!r}) blocked: {e.name}")
