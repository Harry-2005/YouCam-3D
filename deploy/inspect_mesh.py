"""Print connected-component metrics for a GLB mesh."""

import argparse
import json

import numpy as np
import trimesh

parser = argparse.ArgumentParser()
parser.add_argument("mesh")
args = parser.parse_args()
mesh = trimesh.load(args.mesh, force="mesh")
components = trimesh.graph.connected_components(
    mesh.face_adjacency,
    nodes=np.arange(len(mesh.faces)),
    min_len=1,
)
rows = []
for index, faces in enumerate(components):
    vertices = np.unique(mesh.faces[faces].reshape(-1))
    bounds = np.stack((mesh.vertices[vertices].min(axis=0), mesh.vertices[vertices].max(axis=0)))
    rows.append({
        "id": index,
        "faces": int(len(faces)),
        "vertices": int(len(vertices)),
        "bounds": bounds.round(4).tolist(),
        "center": bounds.mean(axis=0).round(4).tolist(),
        "extent": np.ptp(bounds, axis=0).round(4).tolist(),
    })
rows.sort(key=lambda row: row["faces"], reverse=True)
print(json.dumps({"faces": len(mesh.faces), "vertices": len(mesh.vertices), "components": rows[:40]}, indent=2))
