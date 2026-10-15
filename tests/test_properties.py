import pytest
from hypothesis import given, settings, strategies as st

from jsbox import Runtime

RT = Runtime()

scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**53) + 1, max_value=2**53 - 1),
    st.floats(allow_nan=False, allow_infinity=False, width=64),
    st.text(st.characters(blacklist_categories=("Cs",)), max_size=50),
    st.binary(max_size=50),
)
values = st.recursive(
    scalars,
    lambda c: st.one_of(st.lists(c, max_size=5), st.dictionaries(st.text(max_size=8), c, max_size=5)),
    max_leaves=25,
)


def normalize(v):
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, list):
        return [normalize(x) for x in v]
    if isinstance(v, dict):
        return {k: normalize(x) for k, x in v.items()}
    return v


@settings(max_examples=300, deadline=None)
@given(values)
def test_roundtrip(value):
    RT.bind_object("v", value)
    assert RT.eval("v") == normalize(value)


@settings(max_examples=200, deadline=None)
@given(st.integers(min_value=-(2**62), max_value=2**62))
def test_int_roundtrip(n):
    RT.bind_object("n", n)
    assert RT.eval("n") == n


@settings(max_examples=200, deadline=None)
@given(st.text(max_size=200))
def test_eval_arbitrary_source_never_crashes_the_process(src):
    rt = Runtime()
    try:
        rt.eval(src, timeout=0.2)
    except Exception as e:  # noqa: BLE001
        assert type(e).__module__ != "pyo3_runtime", e
    assert rt.eval("1", timeout=1) == 1


@settings(max_examples=100, deadline=None)
@given(st.lists(st.integers(-1000, 1000), max_size=200))
def test_host_function_sums_like_python(xs):
    rt = Runtime()
    rt.bind_function("add", lambda a, b: a + b)
    rt.bind_object("xs", xs)
    assert rt.eval("xs.reduce((a, x) => add(a, x), 0)") == sum(xs)
