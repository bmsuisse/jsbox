import base64

import pytest

from jsbox import JavaScriptError, Runtime


@pytest.fixture
def rt():
    return Runtime(lossy=True)


@pytest.mark.parametrize("text", ["", "ascii", "héllo", "日本語", "🎉 emoji", "a\x00b", "\u2028"])
def test_text_encoder_matches_python(rt, text):
    rt.bind_object("s", text)
    assert rt.eval("new TextEncoder().encode(s)") == text.encode()


@pytest.mark.parametrize("text", ["", "ascii", "héllo", "日本語", "🎉 emoji", "a\x00b"])
def test_text_decoder_roundtrip(rt, text):
    rt.bind_object("b", text.encode())
    assert rt.eval("new TextDecoder().decode(b)") == text


def test_text_decoder_edge_cases(rt):
    assert rt.eval("new TextDecoder().decode()") == ""
    assert rt.eval("new TextDecoder().decode(new Uint8Array([0xEF, 0xBB, 0xBF, 0x41]))") == "A"
    assert rt.eval("new TextDecoder().decode(new Uint8Array([0xFF, 0x41]))") == "\ufffdA"
    assert rt.eval("new TextDecoder().decode(new Uint8Array([1, 2, 3, 4]).subarray(1, 3))") == "\x02\x03"
    assert rt.eval("new TextDecoder().decode(new Uint8Array([65, 66]).buffer)") == "AB"
    assert rt.eval("new TextDecoder('UTF-8').encoding") == "utf-8"
    with pytest.raises(JavaScriptError):
        rt.eval("new TextDecoder('latin1')")


def test_text_encoder_large_input_is_fast(rt):
    assert rt.eval("new TextEncoder().encode('é'.repeat(2e6)).length", timeout=5) == 4_000_000


@pytest.mark.parametrize("raw", [b"", b"a", b"ab", b"abc", bytes(range(256))])
def test_btoa_atob_match_python(rt, raw):
    latin1 = raw.decode("latin1")
    rt.bind_object("s", latin1)
    assert rt.eval("btoa(s)") == base64.b64encode(raw).decode()
    rt.bind_object("e", base64.b64encode(raw).decode())
    assert rt.eval("atob(e)") == latin1


def test_atob_tolerates_whitespace_and_missing_padding(rt):
    assert rt.eval("atob(' YW Jj\\n')") == "abc"
    assert rt.eval("atob('YQ')") == "a"


def test_btoa_rejects_wide_characters_and_atob_garbage(rt):
    with pytest.raises(JavaScriptError, match="Latin1"):
        rt.eval("btoa('\u65e5')")
    with pytest.raises(JavaScriptError, match="base64"):
        rt.eval("atob('***')")


def test_globals_are_not_enumerable_and_helpers_not_leaked(rt):
    names = rt.eval("Object.keys(globalThis)")
    assert not {"TextEncoder", "TextDecoder", "btoa", "atob"} & set(names)
    assert rt.eval("typeof enc + typeof dec + typeof b2a + typeof a2b") == "undefinedundefinedundefinedundefined"
