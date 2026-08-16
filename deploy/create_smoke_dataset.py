"""Create a tiny synthetic Nerfstudio dataset for deployment smoke tests."""

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image


root = Path("/workspace/poster")
images = root / "images"
images.mkdir(parents=True, exist_ok=True)
frames = []

for index in range(12):
    angle = 2 * math.pi * index / 12
    x = np.linspace(0, 1, 64, dtype=np.float32)
    y = np.linspace(0, 1, 64, dtype=np.float32)[:, None]
    phase = (math.sin(angle) + 1) / 2
    rgb = np.stack(
        [
            np.broadcast_to((x + phase) % 1, (64, 64)),
            np.broadcast_to((y + index / 12) % 1, (64, 64)),
            (np.broadcast_to(x, (64, 64)) + np.broadcast_to(y, (64, 64))) / 2,
        ],
        axis=-1,
    )
    image_path = images / f"{index:03d}.png"
    Image.fromarray((rgb * 255).astype(np.uint8)).save(image_path)

    position = np.array([2.0 * math.cos(angle), 2.0 * math.sin(angle), 0.3])
    forward = -position / np.linalg.norm(position)
    up_hint = np.array([0.0, 0.0, 1.0])
    right = np.cross(forward, up_hint)
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    transform = np.eye(4)
    transform[:3, 0] = right
    transform[:3, 1] = up
    transform[:3, 2] = -forward
    transform[:3, 3] = position
    frames.append(
        {
            "file_path": f"images/{index:03d}.png",
            "transform_matrix": transform.tolist(),
        }
    )

(root / "transforms.json").write_text(
    json.dumps(
        {
            "camera_model": "OPENCV",
            "fl_x": 55.0,
            "fl_y": 55.0,
            "cx": 32.0,
            "cy": 32.0,
            "w": 64,
            "h": 64,
            "frames": frames,
        },
        indent=2,
    )
)
