import pytest

from jsbox import JavaScriptError


def test_export_values(rt):
    rt.add_static_module("m", "export const a = 1; export default 'd'; export function f() {}")
    ns = rt.eval_module("m")
    assert ns["a"] == 1 and ns["default"] == "d"
    assert "f" not in ns  # functions are not convertible and are skipped


def test_import_chain(rt):
    rt.add_static_module("math", "export const answer = 42;")
    rt.add_static_module("app", "import {answer} from 'math'; export const x = answer + 1;")
    assert rt.eval_module("app")["x"] == 43


def test_diamond_import_evaluates_dependency_once(rt):
    rt.add_static_module("base", "globalThis.n = (globalThis.n ?? 0) + 1; export const v = 1;")
    rt.add_static_module("l", "import {v} from 'base'; export const l = v;")
    rt.add_static_module("r", "import {v} from 'base'; export const r = v;")
    rt.add_static_module("top", "import {l} from 'l'; import {r} from 'r'; export const t = l + r;")
    assert rt.eval_module("top")["t"] == 2
    assert rt.eval("n") == 1


def test_circular_imports(rt):
    rt.add_static_module("a", "import {b} from 'b'; export const a = 1; export const sum = () => 0;")
    rt.add_static_module("b", "import {a} from 'a'; export const b = 2;")
    assert rt.eval_module("a")["a"] == 1


def test_missing_module(rt):
    with pytest.raises(JavaScriptError, match="not found"):
        rt.eval_module("missing")


def test_missing_import_inside_module(rt):
    rt.add_static_module("app", "import {x} from 'nope'; export const y = x;")
    with pytest.raises(JavaScriptError, match="not found"):
        rt.eval_module("app")


def test_import_of_non_registered_specifier_cannot_reach_files(rt):
    rt.add_static_module("app", "import * as m from '/etc/passwd'; export const y = 1;")
    with pytest.raises(JavaScriptError):
        rt.eval_module("app")
    rt.add_static_module("app2", "import * as m from './secret.js'; export const y = 1;")
    with pytest.raises(JavaScriptError):
        rt.eval_module("app2")


def test_dynamic_import_only_sees_registered_modules(rt):
    rt.add_static_module("ok", "export const v = 5;")
    assert rt.eval("import('ok').then(m => m.v)") == 5
    with pytest.raises(JavaScriptError):
        rt.eval("import('fs')")


def test_module_throws(rt):
    rt.add_static_module("bad", "throw new Error('module boom')")
    with pytest.raises(JavaScriptError, match="module boom"):
        rt.eval_module("bad")


def test_module_syntax_error(rt):
    rt.add_static_module("bad", "export const = ;")
    with pytest.raises(JavaScriptError):
        rt.eval_module("bad")


def test_module_cannot_spoof_result_through_globals(rt):
    rt.add_static_module(
        "evil",
        "export const real = 1; globalThis.__jsbox_ns = {real: 'spoofed'};",
    )
    assert rt.eval_module("evil") == {"real": 1}


def test_odd_module_names(rt):
    for name in ['we"ird', "back\\slash", "uni\u2028code", "a'b", "party\U0001F389"]:
        rt.add_static_module(name, "export const v = 7;")
        assert rt.eval_module(name)["v"] == 7


def test_module_timeout(rt):
    from jsbox import JsTimeoutError

    rt.add_static_module("loop", "while (true) {}")
    with pytest.raises(JsTimeoutError):
        rt.eval_module("loop", timeout=0.1)


def test_top_level_await_in_module(rt):
    rt.add_static_module("tla", "export const v = await Promise.resolve(9);")
    assert rt.eval_module("tla")["v"] == 9


def test_module_sees_host_bindings(rt):
    rt.bind_function("host", lambda: "H")
    rt.add_static_module("m", "export const v = host();")
    assert rt.eval_module("m")["v"] == "H"
