"""Generate a coherent textured GLB from four synthetic turntable views.

This is the geometry-first path for prompt/reference-image jobs. Nerfstudio remains
the reconstruction path for real photographs with genuine camera correspondence.
"""

from __future__ import annotations

import argparse
import gc
import json
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from scipy.spatial import cKDTree
import trimesh

from hy3dgen.shapegen.postprocessors import FaceReducer
from hy3dgen.shapegen import Hunyuan3DDiTFlowMatchingPipeline
from hy3dgen.texgen import Hunyuan3DPaintPipeline


VIEW_INDEX = {"front": 0, "left": 3, "back": 6, "right": 9}


def load_masked_view(dataset: Path, index: int) -> Image.Image:
    image = Image.open(dataset / "images" / f"{index:03d}.png").convert("RGBA")
    mask = Image.open(dataset / "masks" / f"{index:03d}.png").convert("L")
    if mask.size != image.size:
        mask = mask.resize(image.size, Image.Resampling.NEAREST)
    image.putalpha(mask)
    return image


def stage(message: str) -> None:
    print(f"[stage] {message}", flush=True)


def retain_subject_components(mesh: trimesh.Trimesh, proximity: float) -> tuple[trimesh.Trimesh, dict]:
    """Keep the main subject and only genuinely attached nearby components.

    Hunyuan occasionally emits a large studio-background sheet. Its face count
    is too high for a normal "remove small floaters" pass, so the largest
    connected component is treated as the subject and proximity decides which
    accessories remain attached to it.
    """
    proxy = trimesh.Trimesh(vertices=mesh.vertices.copy(), faces=mesh.faces.copy(), process=False)
    proxy.merge_vertices(digits_vertex=5)
    components = list(
        trimesh.graph.connected_components(
            proxy.face_adjacency,
            nodes=np.arange(len(proxy.faces)),
            min_len=1,
        )
    )
    if not components:
        return mesh, {"components": 0, "kept_components": 0, "removed_faces": 0}

    primary = max(components, key=len)
    primary_vertices = np.unique(mesh.faces[primary].reshape(-1))
    tree = cKDTree(mesh.vertices[primary_vertices])
    keep = np.zeros(len(mesh.faces), dtype=bool)
    keep[primary] = True
    kept_components = 1
    for faces in components:
        if faces is primary:
            continue
        vertices = np.unique(mesh.faces[faces].reshape(-1))
        distance = float(tree.query(mesh.vertices[vertices], k=1, workers=-1)[0].min())
        if distance <= proximity:
            keep[faces] = True
            kept_components += 1

    original_faces = len(mesh.faces)
    mesh.update_faces(keep)
    mesh.remove_unreferenced_vertices()
    return mesh, {
        "components": len(components),
        "kept_components": kept_components,
        "removed_faces": int(original_faces - len(mesh.faces)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=12345)
    parser.add_argument("--steps", type=int, default=35)
    parser.add_argument("--octree-resolution", type=int, default=380)
    parser.add_argument("--target-faces", type=int, default=150_000)
    parser.add_argument("--component-proximity", type=float, default=0.06)
    parser.add_argument("--render-size", type=int, default=1024)
    parser.add_argument("--texture-size", type=int, default=1024)
    parser.add_argument("--skip-delight", action="store_true")
    args = parser.parse_args()

    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    torch.set_float32_matmul_precision("high")
    args.output.mkdir(parents=True, exist_ok=True)
    views = {name: load_masked_view(args.dataset, index) for name, index in VIEW_INDEX.items()}
    for name, image in views.items():
        image.save(args.output / f"input-{name}.png")

    started = time.time()
    stage("loading Hunyuan3D multiview geometry model on CUDA")
    shape = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(
        "tencent/Hunyuan3D-2mv",
        subfolder="hunyuan3d-dit-v2-mv",
        variant="fp16",
    )
    stage(f"generating geometry ({args.steps} steps, octree {args.octree_resolution})")
    with torch.inference_mode():
        mesh = shape(
            image=views,
            num_inference_steps=args.steps,
            octree_resolution=args.octree_resolution,
            num_chunks=20_000,
            generator=torch.manual_seed(args.seed),
            output_type="trimesh",
        )[0]
    raw_faces = int(len(mesh.faces))
    raw_vertices = int(len(mesh.vertices))
    mesh.export(args.output / "geometry-raw.glb")
    geometry_seconds = time.time() - started

    del shape
    gc.collect()
    torch.cuda.empty_cache()

    optimize_started = time.time()
    stage(f"reducing {raw_faces:,} faces before CPU-bound component and UV processing")
    if len(mesh.faces) > args.target_faces:
        mesh = FaceReducer()(mesh, max_facenum=args.target_faces)
    reduced_faces = int(len(mesh.faces))
    stage(f"isolating the subject from {reduced_faces:,} reduced faces")
    mesh, component_report = retain_subject_components(mesh, args.component_proximity)
    subject_faces = int(len(mesh.faces))
    mesh.export(args.output / "geometry.glb")
    optimize_seconds = time.time() - optimize_started

    texture_started = time.time()
    stage(
        f"loading CUDA texture models and baking {len(mesh.faces):,} faces at "
        f"{args.texture_size}px"
    )
    paint = Hunyuan3DPaintPipeline.from_pretrained("tencent/Hunyuan3D-2")
    paint.config.render_size = args.render_size
    paint.config.texture_size = args.texture_size
    paint.render.set_default_render_resolution(args.render_size)
    paint.render.set_default_texture_resolution(args.texture_size)
    paint.render.bake_unreliable_kernel_size = max(1, round(2 * args.render_size / 512))
    if args.skip_delight:
        # The generated orbit already uses uniform shadowless studio lighting.
        # The stock 50-step de-light diffusion pass is redundant for this input.
        delight = paint.models.pop("delight_model", None)
        if delight is not None:
            del delight
            gc.collect()
            torch.cuda.empty_cache()
        paint.models["delight_model"] = lambda image: image
    with torch.inference_mode():
        mesh = paint(mesh, image=views["front"])
    mesh.export(args.output / "model.glb")
    texture_seconds = time.time() - texture_started

    report = {
        "engine": "Hunyuan3D-2mv + Hunyuan3D-Paint",
        "seed": args.seed,
        "steps": args.steps,
        "octree_resolution": args.octree_resolution,
        "target_faces": args.target_faces,
        "render_size": args.render_size,
        "texture_size": args.texture_size,
        "skip_delight": args.skip_delight,
        "geometry_seconds": round(geometry_seconds, 2),
        "mesh_optimization_seconds": round(optimize_seconds, 2),
        "texture_seconds": round(texture_seconds, 2),
        "total_seconds": round(time.time() - started, 2),
        "raw_vertices": raw_vertices,
        "raw_faces": raw_faces,
        "reduced_faces": reduced_faces,
        "subject_faces": subject_faces,
        "component_cleanup": component_report,
        "vertices": int(len(mesh.vertices)),
        "faces": int(len(mesh.faces)),
    }
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    stage("textured GLB complete")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
