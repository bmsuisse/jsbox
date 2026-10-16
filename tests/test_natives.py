"""Opt-in native `Buffer` (Runtime(buffer=True)): the global JSZip/pptxgenjs detect."""

import base64
import io
import os
import zipfile
from pathlib import Path

import pytest

from jsbox import JavaScriptError, Runtime


@pytest.fixture
def rt():
    return Runtime(lossy=True, buffer=True)


def test_off_by_default_and_no_ambient_surface():
    r = Runtime()
    assert r.eval("typeof Buffer") == "undefined"
    base = set(r.eval("Object.getOwnPropertyNames(globalThis)"))
    on = set(Runtime(buffer=True).eval("Object.getOwnPropertyNames(globalThis)"))
    assert on - base == {"Buffer"}  # helpers are closure arguments, not globals


def test_buffer_is_not_enumerable_and_is_a_uint8array(rt):
    assert rt.eval("Object.keys(globalThis).includes('Buffer')") is False
    assert rt.eval("(() => { let n = 0; for (const k in globalThis) if (k === 'Buffer') n++; return n })()") == 0
    assert rt.eval("Buffer.from('a') instanceof Uint8Array && Buffer.isBuffer(Buffer.from('a'))") is True
    assert rt.eval("Buffer.isBuffer(new Uint8Array(1))") is False


@pytest.mark.parametrize("text", ["", "ascii", "héllo", "日本語", "🎉 emoji", "a\x00b", "﻿bom"])
def test_utf8_roundtrip_keeps_bom_like_node(rt, text):
    rt.bind_object("s", text)
    assert rt.eval("Buffer.from(s).toString()") == text
    assert rt.eval("Buffer.from(s, 'utf-8')") == text.encode()


@pytest.mark.parametrize("raw", [b"", b"a", b"ab", b"abc", bytes(range(256))])
def test_base64_hex_latin1_match_python(rt, raw):
    rt.bind_object("b", raw)
    b64 = base64.b64encode(raw).decode()
    assert rt.eval("Buffer.from(b).toString('base64')") == b64
    assert rt.eval("Buffer.from(b).toString('base64url')") == base64.urlsafe_b64encode(raw).decode().rstrip("=")
    assert rt.eval("Buffer.from(b).toString('hex')") == raw.hex()
    assert rt.eval("Buffer.from(b).toString('latin1')") == raw.decode("latin1")
    assert rt.eval(f"Buffer.from('{b64}', 'base64')") == raw
    assert rt.eval(f"Buffer.from('{raw.hex()}', 'hex')") == raw
    rt.bind_object("l", raw.decode("latin1"))
    assert rt.eval("Buffer.from(l, 'latin1')") == raw


def test_decoders_are_lenient_like_node(rt):
    assert rt.eval("Buffer.from('YW Jj\\n', 'base64')") == b"abc"  # junk skipped
    assert rt.eval("Buffer.from('YWJj', 'base64url')") == b"abc"
    assert rt.eval("Buffer.from('-_-_', 'base64')") == base64.urlsafe_b64decode("-_-_")
    assert rt.eval("Buffer.from('YQ', 'base64')") == b"a"  # unpadded
    assert rt.eval("Buffer.from('616g62', 'hex')") == b"a"  # stops at first bad pair
    assert rt.eval("Buffer.from('abc', 'hex')") == b"\xab"  # odd length drops the tail
    with pytest.raises(JavaScriptError):
        rt.eval("Buffer.from('x', 'utf-16le')")


def test_from_variants_and_views(rt):
    assert rt.eval("Buffer.from([1, 2, 300])") == b"\x01\x02\x2c"
    assert rt.eval("Buffer.from({type: 'Buffer', data: [7, 8]})") == b"\x07\x08"
    assert rt.eval("(() => { const ab = new Uint8Array([1,2,3,4]).buffer; const b = Buffer.from(ab, 1, 2); return b })()") == b"\x02\x03"
    # ArrayBuffer source is shared, typed-array source is copied (Node semantics).
    assert rt.eval("(() => { const u = new Uint8Array([1]); const b = Buffer.from(u.buffer); b[0] = 9; return u[0] })()") == 9
    assert rt.eval("(() => { const u = new Uint8Array([1]); const b = Buffer.from(u); b[0] = 9; return u[0] })()") == 1
    assert rt.eval("(() => { const b = Buffer.from('abcd'); const s = b.slice(1, 3); s[0] = 0x7a; return b.toString() })()") == "azcd"
    assert rt.eval("Buffer.from('abcd').subarray(1, 3) instanceof Buffer") is True
    with pytest.raises(JavaScriptError):
        rt.eval("Buffer.from(5)")


def test_alloc_concat_compare(rt):
    assert rt.eval("Buffer.alloc(3)") == b"\0\0\0"
    assert rt.eval("Buffer.alloc(5, 'ab')") == b"ababa"
    assert rt.eval("Buffer.alloc(3, 7)") == b"\x07\x07\x07"
    assert rt.eval("Buffer.concat([Buffer.from('ab'), new Uint8Array([99]), Buffer.from('d')])") == b"abcd"
    assert rt.eval("Buffer.concat([Buffer.from('abc'), Buffer.from('def')], 4)") == b"abcd"
    assert rt.eval("Buffer.concat([Buffer.from('ab')], 4)") == b"ab\0\0"
    assert rt.eval("Buffer.concat([])") == b""
    assert rt.eval("Buffer.byteLength('é')") == 2
    assert rt.eval("[Buffer.compare(Buffer.from('a'), Buffer.from('b')), Buffer.from('a').equals(Buffer.from('a')), Buffer.from('ab').compare(Buffer.from('a'))]") == [-1, True, 1]
    with pytest.raises(JavaScriptError):
        rt.eval("Buffer.alloc(-1)")


def test_toString_ranges_and_json(rt):
    assert rt.eval("Buffer.from('hello').toString('utf8', 1, 3)") == "el"
    assert rt.eval("Buffer.from('hello').toString('utf8', 3, 1)") == ""
    assert rt.eval("JSON.stringify(Buffer.from([1, 2]))") == '{"type":"Buffer","data":[1,2]}'


def test_read_write_integers(rt):
    assert rt.eval("(() => { const b = Buffer.alloc(8); b.writeUInt32BE(0xdeadbeef, 0); b.writeInt16LE(-2, 4); return [b.readUInt32BE(0), b.readUInt32LE(0), b.readInt16LE(4), b.readUInt8(4)] })()") == [0xDEADBEEF, 0xEFBEADDE, -2, 0xFE]
    for bad in ["Buffer.alloc(2).readUInt32LE(0)", "Buffer.alloc(2).writeUInt8(256, 0)", "Buffer.alloc(2).readUInt8(2)", "Buffer.alloc(2).readUInt8(0.5)"]:
        with pytest.raises(JavaScriptError):
            rt.eval(bad)


def test_write_copy_indexof(rt):
    assert rt.eval("(() => { const b = Buffer.alloc(4); return [b.write('héy', 1), b.toString('hex')] })()") == [3, "0068c3a9"]
    assert rt.eval("(() => { const b = Buffer.from('abcdef'); const t = Buffer.alloc(3); return [b.copy(t, 1, 2, 4), t.toString()] })()") == [2, "\0cd"]
    assert rt.eval("[Buffer.from('abcabc').indexOf('ca'), Buffer.from('abc').indexOf(99), Buffer.from('abc').includes('z')]") == [2, 2, False]


def test_memory_is_bounded_by_the_runtime_limit():
    r = Runtime(memory_limit=16 << 20, buffer=True)
    with pytest.raises(JavaScriptError):
        r.eval("Buffer.alloc(1 << 30)")
    with pytest.raises(JavaScriptError):
        r.eval("Buffer.from('x'.repeat(1 << 20)).toString('hex').repeat(64).length")
    assert r.eval("1 + 1") == 2  # still usable


def test_buffer_returns_to_python_as_bytes(rt):
    out = rt.eval("Buffer.from('PK')")
    assert type(out) is bytes and out == b"PK"


ASSETS = Path(os.environ.get("JSBOX_ONEHUB_JS", Path.home() / "DevOps/OneHub/assets/js"))


@pytest.mark.skipif(not ASSETS.is_dir(), reason="OneHub assets not available")
def test_pptxgenjs_deck_identical_with_and_without_buffer():
    pre = (ASSETS / "pydeno_shims.js").read_text() + "\n;\n" + (ASSETS / "pptxgenjs-3.12.0.bundle.js").read_text() + "\n;\n"
    code = """(async () => { const p = new PptxGenJS(); const s = p.addSlide();
      s.addText('Grüße 日本語 🎉', {x: 1, y: 1, w: 5, h: 1});
      s.addChart(p.charts.BAR, [{name: 'ä', labels: ['a', 'b'], values: [1, 2]}], {x: 1, y: 2, w: 5, h: 3});
      return await p.write({outputType: 'base64'}) })()"""

    def files(**kw):
        raw = base64.b64decode(Runtime(memory_limit=256 << 20, **kw).eval(pre + code, timeout=60))
        z = zipfile.ZipFile(io.BytesIO(raw))
        assert z.testzip() is None
        out = {n: z.read(n) for n in z.namelist() if n != "docProps/core.xml" and not n.endswith(".xlsx")}
        for n in z.namelist():  # embedded workbooks carry a build timestamp: compare their parts
            if n.endswith(".xlsx"):
                inner = zipfile.ZipFile(io.BytesIO(z.read(n)))
                out.update({n + "!" + m: inner.read(m) for m in inner.namelist() if m != "docProps/core.xml"})
        return out

    a, b = files(), files(buffer=True)
    assert a == b
    assert "Grüße 日本語 🎉" in b["ppt/slides/slide1.xml"].decode()
