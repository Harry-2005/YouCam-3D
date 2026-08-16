"""Resume Hunyuan3D-Paint from an already generated geometry GLB."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import torch
import trimesh
from PIL import Image

from hy3dgen.texgen import Hunyuan3DPaintPipeline


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--geometry", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    mesh = trimesh.load(args.geometry, force="mesh")
    image = Image.open(args.image).convert("RGBA")
    started = time.time()
    paint = Hunyuan3DPaintPipeline.from_pretrained("tencent/Hunyuan3D-2")
    mesh = paint(mesh, image=image)
    mesh.export(args.output)
    print(json.dumps({
        "texture_seconds": round(time.time() - started, 2),
        "vertices": int(len(mesh.vertices)),
        "faces": int(len(mesh.faces)),
        "output": str(args.output),
    }))


if __name__ == "__main__":
    main()
