"""Remove mask-floor residue and disconnected floaters from a textured GLB."""

import argparse
import json

import numpy as np
import trimesh
from scipy.spatial import cKDTree

parser = argparse.ArgumentParser()
parser.add_argument("source")
parser.add_argument("output")
parser.add_argument("--min-faces", type=int, default=3000)
parser.add_argument("--proximity", type=float, default=0.04)
args = parser.parse_args()

mesh = trimesh.load(args.source, force="mesh")

# Textured GLBs duplicate vertices along UV seams. Computing connected
# components directly on the textured mesh therefore mistakes thousands of UV
# islands for detached geometry and punches holes when small islands are
# removed. Build a position-only proxy, weld coincident seam vertices there,
# and use its face groups to select faces from the untouched textured mesh.
proxy = trimesh.Trimesh(
    vertices=mesh.vertices.copy(),
    faces=mesh.faces.copy(),
    process=False,
)
proxy.merge_vertices(digits_vertex=5)
components = trimesh.graph.connected_components(
    proxy.face_adjacency,
    nodes=np.arange(len(proxy.faces)),
    min_len=1,
)
keep = np.zeros(len(mesh.faces), dtype=bool)
kept_components = 0
removed_floor_components = 0
removed_small_components = 0
component_rows = []
for faces in components:
    vertices = np.unique(mesh.faces[faces].reshape(-1))
    bounds = np.stack((mesh.vertices[vertices].min(axis=0), mesh.vertices[vertices].max(axis=0)))
    extent = np.ptp(bounds, axis=0)
    center = bounds.mean(axis=0)
    is_mask_floor = extent[1] < 0.035 and center[1] < -0.34
    component_rows.append((faces, vertices, is_mask_floor))

candidates = [row for row in component_rows if not row[2]]
if not candidates:
    raise RuntimeError("Mesh cleanup found no non-floor geometry")
primary = max(candidates, key=lambda row: len(row[0]))
primary_faces, primary_vertices, _ = primary

tree = cKDTree(mesh.vertices[primary_vertices])
for faces, vertices, is_mask_floor in component_rows:
    if is_mask_floor:
        removed_floor_components += 1
    else:
        near_subject = faces is primary_faces
        if not near_subject:
            distance = tree.query(mesh.vertices[vertices], k=1, workers=-1)[0].min()
            near_subject = distance <= args.proximity
        if near_subject:
            keep[faces] = True
            kept_components += 1
        else:
            removed_small_components += 1

mesh.update_faces(keep)
mesh.remove_unreferenced_vertices()
mesh.export(args.output)
print(json.dumps({
    "vertices": int(len(mesh.vertices)),
    "faces": int(len(mesh.faces)),
    "welded_proxy_vertices": int(len(proxy.vertices)),
    "geometric_components": int(len(components)),
    "kept_components": kept_components,
    "primary_faces": int(len(primary_faces)),
    "removed_floor_components": removed_floor_components,
    "removed_small_components": removed_small_components,
}))
