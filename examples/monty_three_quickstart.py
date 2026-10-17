"""Smallest Monty + jsbox + three.js pipeline: Python crunches data, JavaScript builds a 3D model.

    pip install jsbox pydantic-monty
    python examples/monty_three_quickstart.py        # writes terrain.glb
"""

import asyncio
from pathlib import Path

from _sandbox import load_libs, new_runtime
from pydantic_monty import Monty

# 1. Model-written Python (runs in Monty) crunches the data: a 24x24 height field.
with Monty() as pool, pool.checkout() as session:
    heights = session.feed_run("""
import math
[[round(math.sin(x / 3) * math.cos(y / 3) * 2, 3) for x in range(24)] for y in range(24)]
""")

# 2. Model-written JavaScript (runs in jsbox) turns it into a three.js scene and a .glb file.
rt = new_runtime()
load_libs(rt, "three-0.180.0-gltf.bundle.js")
rt.bind_object("heights", heights)
glb = asyncio.run(rt.eval_async("""
(async () => {
  const n = heights.length, geo = new THREE.PlaneGeometry(10, 10, n - 1, n - 1);
  const pos = geo.attributes.position;
  heights.flat().forEach((h, i) => pos.setZ(i, h));
  geo.computeVertexNormals();
  const scene = new THREE.Scene();
  scene.add(new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: 0x3b8f5f })));
  return await new Promise((ok, fail) => new GLTFExporter().parse(scene, ok, fail, { binary: true }));
})()
""", timeout=60))
Path("terrain.glb").write_bytes(glb)
print(f"terrain.glb: {len(glb)} bytes")
