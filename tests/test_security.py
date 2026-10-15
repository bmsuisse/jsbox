"""The guest has exactly what the host bound, nothing more."""

import pytest

from jsbox import JavaScriptError, Runtime

AMBIENT = [
    "require", "process", "Deno", "Bun", "fetch", "XMLHttpRequest", "WebSocket",
    "std", "os", "scriptArgs", "print", "readFile", "importScripts", "window",
    "document", "navigator", "Buffer", "__dirname", "__filename", "setTimeout",
    "setInterval", "setImmediate", "queueMicrotask_host", "_jsbox", "jsbox",
]


@pytest.mark.parametrize("name", AMBIENT)
def test_no_ambient_authority(rt, name):
    assert rt.eval(f"typeof {name}") == "undefined"


def test_global_names_are_only_standard_builtins(rt):
    names = set(rt.eval("Object.getOwnPropertyNames(globalThis)"))
    suspicious = {n for n in names if n.lower() in {a.lower() for a in AMBIENT}}
    assert not suspicious


def test_eval_and_function_constructor_do_not_escape(rt):
    assert rt.eval("eval('typeof process')") == "undefined"
    assert rt.eval("new Function('return typeof process')()") == "undefined"
    assert rt.eval("(function(){ return this })().process") is None


def test_constructor_chain_does_not_reach_host(rt):
    assert rt.eval("(() => {}).constructor('return typeof require')()") == "undefined"
    assert rt.eval("Object.getPrototypeOf(async function(){}).constructor('return typeof process')()") is not None


def test_bound_function_is_only_what_was_bound(rt):
    rt.bind_function("tool", lambda: "t")
    # No handle on Python via the function object.
    assert rt.eval("typeof tool.constructor") == "function"
    assert rt.eval("Object.getOwnPropertyNames(tool).sort().join()") == "length"


def test_guest_cannot_call_unbound_host_names(rt):
    rt.bind_function("allowed", lambda: 1)
    with pytest.raises(JavaScriptError):
        rt.eval("forbidden()")


def test_prototype_pollution_stays_in_the_guest(rt):
    rt.eval("Object.prototype.polluted = 1")
    assert Runtime().eval("({}).polluted") is None
    assert rt.eval("({}).polluted") == 1
    assert rt.eval("({a: 1})") == {"a": 1}  # inherited props are not exported


def test_guest_global_overwrites_cannot_break_host_conversion(rt):
    rt.eval("globalThis.Array = null; globalThis.Object = null; globalThis.JSON = null")
    assert rt.eval("[1, 2]") == [1, 2]
    assert rt.eval("({a: 1})") == {"a": 1}


def test_tampered_builtins_cannot_forge_host_results(rt):
    rt.bind_function("tool", lambda: "real")
    rt.eval("Function.prototype.call = () => 'forged'; Function.prototype.apply = () => 'forged'; 0")
    assert rt.eval("tool()") == "real"


def test_getter_on_result_runs_under_the_timeout():
    from jsbox import JsTimeoutError

    rt = Runtime()
    with pytest.raises(JsTimeoutError):
        rt.eval("({get a() { for (;;) {} }})", timeout=0.2)


def test_tojson_and_valueof_traps_on_results_are_harmless(rt):
    assert rt.eval("({a: 1, toJSON() { throw 1 }, valueOf() { throw 2 }})") == {"a": 1}


def test_proxy_result_does_not_crash_or_hang():
    rt = Runtime()
    try:
        rt.eval("new Proxy({}, {ownKeys() { throw new Error('trap') }})", timeout=1)
    except (JavaScriptError, ValueError, TypeError):
        pass
    assert rt.eval("1", timeout=1) == 1


def test_error_messages_do_not_leak_host_paths(rt):
    with pytest.raises(JavaScriptError) as ei:
        rt.eval("null.x")
    text = str(ei.value)
    assert "/Users/" not in text and "/home/" not in text and "cargo" not in text


def test_threads_cannot_share_a_runtime(rt):
    import threading

    out = []

    def work():
        try:
            rt.eval("1")
            out.append("ran")
        except BaseException as e:  # noqa: BLE001
            out.append(type(e).__name__)

    t = threading.Thread(target=work)
    t.start()
    t.join()
    assert out and out[0] != "ran"


def test_runtime_per_thread_works():
    import threading

    results = []

    def work(i):
        results.append(Runtime().eval(f"{i} * 2"))

    ts = [threading.Thread(target=work, args=(i,)) for i in range(16)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results) == [i * 2 for i in range(16)]


def test_scripts_run_sloppy_like_a_browser(rt):
    rt.eval("implicit = 5")
    assert rt.eval("implicit") == 5
    assert rt.eval("(function () { return typeof this })()") == "object"
