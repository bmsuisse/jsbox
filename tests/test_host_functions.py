import pytest

from jsbox import JavaScriptError


def test_call_with_various_types(rt):
    seen = []
    rt.bind_function("record", lambda *a: seen.append(a) or "ok")
    assert rt.eval("record(1, 'a', null, [1, 2], {k: true}, new Uint8Array([9]))") == "ok"
    assert seen == [(1, "a", None, [1, 2], {"k": True}, b"\x09")]


def test_return_values(rt):
    rt.bind_function("get", lambda: {"a": [1, 2], "b": b"xy"})
    assert rt.eval("get().a[1]") == 2
    assert rt.eval("get().b.length") == 2


def test_returns_none_becomes_null(rt):
    rt.bind_function("nothing", lambda: None)
    assert rt.eval("nothing() === null") is True


def test_arity_is_the_hosts_business(rt):
    rt.bind_function("two", lambda a, b: a + b)
    with pytest.raises(TypeError):
        rt.eval("two(1)")


def test_many_calls(rt):
    rt.bind_function("add", lambda a, b: a + b)
    assert rt.eval("let s = 0; for (let i = 0; i < 20000; i++) s = add(s, i); s") == sum(range(20000))


def test_original_exception_is_reraised(rt):
    def boom():
        raise KeyError("nope")

    rt.bind_function("boom", boom)
    with pytest.raises(KeyError, match="nope"):
        rt.eval("boom()")


def test_js_can_catch_host_error_and_continue(rt):
    def boom():
        raise RuntimeError("secret internals /etc/passwd")

    rt.bind_function("boom", boom)
    assert rt.eval("try { boom(); 'no' } catch (e) { 'caught' }") == "caught"


def test_guest_cannot_read_host_exception_message(rt):
    def boom():
        raise RuntimeError("secret internals /etc/passwd")

    rt.bind_function("boom", boom)
    msg = rt.eval("try { boom() } catch (e) { e.message + '|' + String(e.stack) }")
    assert "secret" not in msg and "passwd" not in msg
    assert "RuntimeError" in msg and "[#" not in msg


def test_swallowed_host_error_is_not_blamed_for_a_later_js_error(rt):
    def boom():
        raise KeyError("stale")

    rt.bind_function("boom", boom)
    with pytest.raises(JavaScriptError, match="later"):
        rt.eval("try { boom() } catch (e) {}; throw new Error('later')")


def test_host_error_state_does_not_leak_into_next_eval(rt):
    rt.bind_function("boom", lambda: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        rt.eval("boom()")
    with pytest.raises(JavaScriptError, match="fresh"):
        rt.eval("throw new Error('fresh')")


def test_rethrown_host_error_by_guest_still_maps_to_original(rt):
    rt.bind_function("boom", lambda: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        rt.eval("try { boom() } catch (e) { throw e }")


def test_async_host_function_is_rejected_clearly(rt):
    async def f():
        return 1

    rt.bind_function("f", f)
    with pytest.raises(TypeError, match="async"):
        rt.eval("f()")


def test_unconvertible_return_is_a_host_error(rt):
    rt.bind_function("bad", lambda: object())
    with pytest.raises(TypeError):
        rt.eval("bad()")


def test_rebinding_replaces(rt):
    rt.bind_function("f", lambda: 1)
    rt.bind_function("f", lambda: 2)
    assert rt.eval("f()") == 2


def test_host_function_can_use_the_runtime_reentrantly_only_via_new_runtime(rt):
    from jsbox import Runtime

    rt.bind_function("inner", lambda: Runtime().eval("40 + 2"))
    assert rt.eval("inner()") == 42


def test_host_function_as_callback(rt):
    rt.bind_function("apply", lambda x, *_: x * 2)
    assert rt.eval("[1, 2, 3].map(apply)") == [2, 4, 6]


def test_guest_cannot_forge_host_error_attribution(rt):
    def boom():
        raise KeyError("stale")

    rt.bind_function("boom", boom)
    # Look-alike message copied from what a host error shows the guest.
    with pytest.raises(JavaScriptError):
        rt.eval("let m; try { boom() } catch (e) { m = e.message } throw new Error(m)")
    with pytest.raises(JavaScriptError):
        rt.eval("try { boom() } catch (e) { throw Object.assign(new Error(e.message), {name: e.name}) }")
