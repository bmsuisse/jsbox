import time

import pytest

from jsbox import JavaScriptError, JsTimeoutError, Runtime, run_killable


@pytest.mark.parametrize(
    "code",
    ["Array(2**32-1).reverse()", "const a=[]; a.length=2**32-1; a.sort()", "Array(2**32-1).join()"],
)
def test_huge_array_natives_are_refused_fast(code):
    t = time.time()
    with pytest.raises(JavaScriptError, match="too large"):
        Runtime().eval(code, timeout=1)
    assert time.time() - t < 1


def test_huge_bigint_parse_is_refused_fast():
    t = time.time()
    with pytest.raises(JavaScriptError, match="too long"):
        Runtime().eval("BigInt('1'.repeat(200000))", timeout=1)
    assert time.time() - t < 1


def test_guards_keep_normal_behaviour():
    out = Runtime().eval("[[3,1,2].sort().join('-'), BigInt('123'), typeof BigInt, BigInt.asIntN(8, 255n), (5n).constructor.name]")
    assert out == ["1-2-3", 123, "function", -1, "BigInt"]
    with pytest.raises(JavaScriptError):
        Runtime().eval("new BigInt(1)")


def test_run_killable_returns_value_and_errors():
    assert run_killable("[1, 2, 3].map(x => x * 2)", timeout=5) == [2, 4, 6]
    with pytest.raises(JavaScriptError) as e:
        run_killable("throw new TypeError('nope')", timeout=5)
    assert e.value.name == "TypeError" and e.value.message == "nope"
    with pytest.raises(JsTimeoutError):
        run_killable("while (true) {}", timeout=0.5)


def test_run_killable_kills_what_timeout_cannot_interrupt():
    # An uninterruptible native loop: the in-process timeout would not fire, the process kill does.
    t = time.time()
    with pytest.raises(JsTimeoutError):
        run_killable("let x = 3n; for (;;) x = x * x", timeout=0.5, grace=0.5, memory_limit=0)
    assert time.time() - t < 10


def test_run_killable_passes_runtime_options():
    with pytest.raises(JavaScriptError):
        run_killable("'x'.repeat(50_000_000).length", timeout=5, memory_limit=8 * 1024 * 1024)
