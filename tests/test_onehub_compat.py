"""OneHub runs vendored browser bundles (pptxgenjs, three, dagre, vega, turf, d3,
bpmn-moddle) plus host-side shims inside the sandbox. These tests run those exact
files on jsbox; they are skipped when the OneHub checkout is not available.

Point JSBOX_ONEHUB_JS at OneHub's assets/js to run them elsewhere.
"""

import asyncio
import base64
import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from jsbox import Runtime

ASSETS = Path(os.environ.get("JSBOX_ONEHUB_JS", Path.home() / "DevOps/OneHub/assets/js"))
pytestmark = pytest.mark.skipif(not ASSETS.is_dir(), reason="OneHub assets not available")

BUNDLES = {
    "pptxgenjs": "pptxgenjs-3.12.0.bundle.js",
    "three": "three-0.180.0-gltf.bundle.js",
    "dagre": "dagre-3.1.1.min.js",
    "vega": "vega-6.4.0-lite-6.4.3.bundle.js",
    "turf": "turf-7.4.0.bundle.js",
    "d3": "d3-force-3.0.0-delaunay-6.0.4.bundle.js",
}


def prelude(*names):
    """Same assembly as OneHub's pydeno_libraries._library_prelude."""
    parts = [(ASSETS / "pydeno_shims.js").read_text()]
    parts += [(ASSETS / BUNDLES[n]).read_text() for n in names]
    parts.append((ASSETS / "pydeno_deck_helpers.js").read_text())
    return "\n;\n".join(parts) + "\n;\n"


def runtime(**kw):
    return Runtime(memory_limit=256 * 1024 * 1024, **kw)


DECK = """
(async () => {
  const pptx = new PptxGenJS();
  pptx.layout = 'LAYOUT_WIDE';
  const s = pptx.addSlide();
  s.addText('Quarterly review', {x: 0.5, y: 0.4, w: 9, h: 1, fontSize: 28, bold: true});
  s.addTable([[{text: 'Region'}, {text: 'Sales'}], [{text: 'CH'}, {text: '120'}]], {x: 0.5, y: 1.6, w: 6});
  s.addChart(pptx.charts.BAR, [{name: 'Sales', labels: ['CH', 'DE'], values: [120, 90]}], {x: 0.5, y: 3.2, w: 6, h: 3});
  s.addImage({data: 'image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', x: 7, y: 1.6, w: 1, h: 1});
  pptx.addSlide().addText('Second', {x: 1, y: 1, w: 3, h: 1});
  return await pptx.write({outputType: 'base64'});
})()
"""


@pytest.mark.parametrize("name", sorted(BUNDLES))
def test_every_bundle_loads_under_the_shims(name):
    runtime().eval(prelude(name))


def test_pptxgenjs_builds_a_valid_deck():
    b64 = runtime().eval(prelude("pptxgenjs") + DECK, timeout=60)
    z = zipfile.ZipFile(io.BytesIO(base64.b64decode(b64)))
    assert z.testzip() is None
    names = set(z.namelist())
    assert {"[Content_Types].xml", "ppt/presentation.xml", "ppt/slides/slide1.xml", "ppt/slides/slide2.xml"} <= names
    assert any(n.startswith("ppt/charts/chart") for n in names)
    assert any(n.startswith("ppt/media/") for n in names)
    assert "Quarterly review" in z.read("ppt/slides/slide1.xml").decode()


def test_pptxgenjs_output_types():
    code = """(async () => { const p = new PptxGenJS(); p.addSlide().addText('x', {x: 1, y: 1, w: 2, h: 1});
      const u8 = await p.write({outputType: 'uint8array'});
      return [u8.constructor.name, u8.length > 1000, u8[0], u8[1]] })()"""
    assert runtime().eval(prelude("pptxgenjs") + code, timeout=60) == ["Uint8Array", True, 80, 75]


def test_pptxgenjs_returns_bytes_to_python():
    code = """(async () => { const p = new PptxGenJS(); p.addSlide().addText('x', {x: 1, y: 1, w: 2, h: 1});
      return await p.write({outputType: 'uint8array'}) })()"""
    out = runtime().eval(prelude("pptxgenjs") + code, timeout=60)
    assert isinstance(out, bytes) and out[:2] == b"PK"
    assert zipfile.ZipFile(io.BytesIO(out)).testzip() is None


def test_three_exports_a_glb():
    code = """(async () => { const s = new THREE.Scene();
      s.add(new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial({color: 0xff0000})));
      const glb = await exportGLB(s); return [glb.length, String.fromCharCode(...glb.slice(0, 4))] })()"""
    n, magic = runtime().eval(prelude("three") + code, timeout=60)
    assert magic == "glTF" and n > 500


def test_dagre_lays_out_a_graph():
    code = """(() => { const g = new dagre.graphlib.Graph(); g.setGraph({rankdir: 'LR'}); g.setDefaultEdgeLabel(() => ({}));
      for (const n of 'abc') g.setNode(n, {width: 40, height: 20}); g.setEdge('a', 'b'); g.setEdge('b', 'c'); dagre.layout(g);
      return ['a', 'b', 'c'].map(n => Math.round(g.node(n).x)) })()"""
    xs = runtime().eval(prelude("dagre") + code)
    assert xs == sorted(xs) and len(set(xs)) == 3


def test_vega_lite_renders_svg_in_the_onehub_theme():
    code = """onehub.vegaSvg({data: {values: [{a: 'x', b: 1}, {a: 'y', b: 3}]}, mark: 'bar',
      encoding: {x: {field: 'a', type: 'nominal'}, y: {field: 'b', type: 'quantitative'}}})"""
    svg = runtime().eval(prelude("vega") + code, timeout=60)
    assert svg.startswith("<svg") and "</svg>" in svg and "2e4a62" in svg.lower()


def test_turf_and_d3():
    out = runtime().eval(
        prelude("turf", "d3")
        + """(() => ({area: Math.round(turf.area(turf.polygon([[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]))),
          scale: d3.scaleLinear().domain([0, 10]).range([0, 100])(5),
          hull: turf.convex(turf.randomPoint(30, {bbox: [0, 0, 1, 1]})).geometry.type}))()"""
    )
    assert out["scale"] == 50 and out["hull"] == "Polygon" and out["area"] > 1e10


def test_d3_delaunay_and_hierarchy():
    code = """(() => { const pts = Array.from({length: 50}, (_, i) => [(i * 37) % 101, (i * 53) % 97]);
      const d = d3.Delaunay.from(pts); const root = d3.hierarchy({children: [{value: 1}, {value: 2}]}).sum(n => n.value || 0);
      return [d.triangles.length > 0, root.value] })()"""
    assert runtime().eval(prelude("d3") + code, timeout=60) == [True, 3]


def test_bpmn_moddle_parses_xml():
    bpmn = (ASSETS / "bpmn-moddle-9.0.1.bundle.js").read_text()
    xml = (
        '<?xml version="1.0"?><bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" id="d">'
        '<bpmn:process id="p"><bpmn:startEvent id="s"/></bpmn:process></bpmn:definitions>'
    )
    code = f"""(async () => {{ const m = new BpmnModdle(); const r = await m.fromXML({json.dumps(xml)});
      return r.rootElement.rootElements[0].flowElements[0].$type }})()"""
    assert runtime().eval((ASSETS / "pydeno_shims.js").read_text() + "\n;\n" + bpmn + "\n;\n" + code) == "bpmn:StartEvent"


# --- the host side: what OneHub's pydeno_worker does around the bundles ----------


async def test_append_slides_through_an_async_call_tool():
    calls = []

    async def call_tool(name, args):
        calls.append((name, args))
        await asyncio.sleep(0)
        cmds = args["commands"]
        if cmds[0].startswith("get "):
            slides = [json.dumps([
                {"command": "add", "type": "slide", "props": {}},
                {"command": "add", "type": "shape", "path": f"/slide[{i}]", "props": {"fill": "000000", "line": "123ABC"}},
            ]) for i in range(1, len(cmds))]
            root = json.dumps({"data": {"results": [{"children": [{"type": "slide"}, {"type": "slide"}]}]}})
            return json.dumps({"url": "u1", "results": [{"stdout": root, "exit_code": 0}] + [{"stdout": s, "exit_code": 0} for s in slides]})
        return json.dumps({"url": "u2", "results": [{"command": "batch", "stdout": "", "exit_code": 0}]})

    rt = runtime(lossy=True)
    rt.bind_function("call_tool", call_tool)
    code = prelude("pptxgenjs") + """
    (async () => {
      const pptx = new PptxGenJS();
      pptx.addSlide().addText('a', {x: 1, y: 1, w: 2, h: 1});
      pptx.addSlide().addText('b', {x: 1, y: 1, w: 2, h: 1});
      return await onehub.appendSlides(pptx, {document_url: 'u0'});
    })()"""
    out = await rt.eval_async(code, timeout=60)
    assert out["url"] == "u2" and out["slides_added"] == 2 and out["first_new_slide"] == 3
    assert any("literal colours 123ABC" in i for i in out["issues"])
    batch_call = calls[-1][1]
    items = json.loads(base64.b64decode(batch_call["attachments"][0]["content_base64"]))
    paths = [i.get("path") for i in items if i["type"] == "shape"]
    assert paths == ["/slide[3]", "/slide[4]"]
    assert all(i["props"]["fill"] == "tx1" for i in items if i["type"] == "shape")
    assert calls[0][1]["attachments"][0]["filename"] == "gen.pptx"
    assert base64.b64decode(calls[0][1]["attachments"][0]["content_base64"])[:2] == b"PK"


async def test_add_model3d_through_call_tool():
    seen = {}

    async def call_tool(name, args):
        seen.update(args)
        return {"url": "u", "results": [{"command": "x", "stdout": "", "exit_code": 0}]}

    rt = runtime(lossy=True)
    rt.bind_function("call_tool", call_tool)
    code = prelude("three") + """(async () => {
      const s = new THREE.Scene(); s.add(new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshBasicMaterial()));
      return await onehub.addModel3d(s, {document_url: 'u0', slide: 2, x: '1in'}) })()"""
    out = await rt.eval_async(code, timeout=60)
    assert out["glb_bytes"] > 500 and out["url"] == "u"
    assert base64.b64decode(seen["attachments"][0]["content_base64"])[:4] == b"glTF"
    assert "--prop x=1in" in seen["commands"][0]


async def test_a_failing_tool_surfaces_as_a_js_error_the_model_can_catch():
    async def call_tool(name, args):
        raise RuntimeError("tool exploded: /srv/secret")

    rt = runtime()
    rt.bind_function("call_tool", call_tool)
    out = await rt.eval_async(
        "(async () => { try { await call_tool('x', {}) } catch (e) { return [e.name, e.message] } })()"
    )
    assert out[0] == "Error" and "secret" not in out[1]


async def test_worker_style_console_and_json_safe_result():
    lines = []
    rt = runtime(on_console=lambda level, args: lines.append((level, args)), lossy=True)
    out = await rt.eval_async(
        "console.log('rows', [1, 2]); console.error('oops'); ({ok: true, bytes: new Uint8Array([1, 2]), fn() {}})"
    )
    assert out == {"ok": True, "bytes": b"\x01\x02", "fn": "[function]"}
    assert lines == [("log", ["rows", [1, 2]]), ("error", ["oops"])]


async def test_many_parallel_tool_calls_like_a_batch_script():
    async def call_tool(name, args):
        await asyncio.sleep(0.01)
        return {"echo": args["i"]}

    rt = runtime()
    rt.bind_function("call_tool", call_tool)
    out = await rt.eval_async(
        "(async () => (await Promise.all(Array.from({length: 32}, (_, i) => call_tool('t', {i})))).map(r => r.echo))()",
        timeout=30,
    )
    assert out == list(range(32))
