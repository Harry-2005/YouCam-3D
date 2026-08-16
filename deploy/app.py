from __future__ import annotations

import asyncio
import base64
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import io
import json
import logging
import math
import mimetypes
import os
import re
import secrets
import shutil
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from google import genai
from google.cloud import storage
from google.genai import types as genai_types
from PIL import Image, ImageFilter, ImageOps
from pydantic import BaseModel, Field


DATA_ROOT = Path(os.getenv("DATA_ROOT", "/var/lib/nerfstudio-api/jobs"))
CACHE_ROOT = Path(os.getenv("CACHE_ROOT", "/var/lib/nerfstudio-api/cache"))
PIPELINE_ROOT = Path(os.getenv("PIPELINE_ROOT", "/var/lib/nerfstudio-api/pipelines"))
STYLE_ROOT = Path(os.getenv("STYLE_ROOT", "/var/lib/nerfstudio-api/style-jobs"))
FRONTEND_ROOT = Path(os.getenv("FRONTEND_ROOT", "/opt/nerfstudio-api/frontend"))
API_KEY = os.environ["API_KEY"]
NERFSTUDIO_IMAGE = os.getenv(
    "NERFSTUDIO_IMAGE", "ghcr.io/nerfstudio-project/nerfstudio:latest"
)
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(10 * 1024**3)))
NANO_BANANA_PROJECT = os.getenv("NANO_BANANA_PROJECT", "project-a2dcdad0-5d65-4d61-846")
NANO_BANANA_LOCATION = os.getenv("NANO_BANANA_LOCATION", "global")
NANO_BANANA_MODEL = os.getenv("NANO_BANANA_MODEL", "gemini-3.1-flash-image")
ALLOWED_METHODS = {"nerfacto", "nerfacto-big", "nerfacto-huge", "splatfacto"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
NANO_BANANA_MODELS = {
    "gemini-3.1-flash-image",
    "gemini-3.1-flash-lite-image",
    "gemini-3-pro-image",
    "gemini-2.5-flash-image",
}
NANO_ASPECT_RATIOS = {"1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9"}
NANO_IMAGE_SIZES = {"1K", "2K", "4K"}
NANO_INPUT_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/heic", "image/heif"}
NANO_MAX_INPUT_BYTES = 50 * 1024**2
PIPELINE_COHERENCE_THRESHOLD = float(os.getenv("PIPELINE_COHERENCE_THRESHOLD", "0.80"))
PIPELINE_MIN_USABLE_COHERENCE = float(os.getenv("PIPELINE_MIN_USABLE_COHERENCE", "0.40"))
PIPELINE_MIN_IDENTITY_SCORE = float(os.getenv("PIPELINE_MIN_IDENTITY_SCORE", "0.75"))
VEO_MODEL = os.getenv("VEO_MODEL", "veo-3.1-fast-generate-001")
VEO_OUTPUT_BUCKET = os.getenv(
    "VEO_OUTPUT_BUCKET", "youcam-parallax-project-a2dcdad0-5d65-4d61-846"
)
PIPELINE_VIEW_COUNT = 12
PIPELINE_ELEVATION = 10.0
PIPELINE_HALF_VIEW_COUNT = PIPELINE_VIEW_COUNT // 2
PIPELINE_MIN_ITERATIONS = int(os.getenv("PIPELINE_MIN_ITERATIONS", "5000"))
NANO_BANANA_ANCHOR_MODEL = os.getenv("NANO_BANANA_ANCHOR_MODEL", "gemini-3-pro-image")
NANO_BANANA_STABILIZER_MODEL = os.getenv(
    "NANO_BANANA_STABILIZER_MODEL", "gemini-3.1-flash-image"
)
PIPELINE_STABILIZATION_PASSES = int(os.getenv("PIPELINE_STABILIZATION_PASSES", "2"))
PIPELINE_STABILIZATION_WORKERS = max(
    1, int(os.getenv("PIPELINE_STABILIZATION_WORKERS", "4"))
)
HUNYUAN_ROOT = Path(os.getenv("HUNYUAN_ROOT", "/opt/hunyuan3d-2"))
HUNYUAN_PYTHON = Path(os.getenv("HUNYUAN_PYTHON", "/opt/hunyuan3d-venv/bin/python"))
YOUCAM_API_BASE = os.getenv("YOUCAM_API_BASE", "https://yce-api-01.makeupar.com").rstrip("/")
YOUCAM_API_KEY_FILE = Path(
    os.getenv("YOUCAM_API_KEY_FILE", "/opt/nerfstudio-api/.youcam-api-key")
)
YOUCAM_TASK_TIMEOUT = int(os.getenv("YOUCAM_TASK_TIMEOUT", "420"))
CLOTHING_PROVIDER_ORDER = [
    item.strip()
    for item in os.getenv("CLOTHING_PROVIDER_ORDER", "youcam").split(",")
    if item.strip()
]
CLOTHING_WEBHOOK_PROVIDERS_JSON = os.getenv("CLOTHING_WEBHOOK_PROVIDERS_JSON", "[]")
STYLE_MAX_MEDIA_BYTES = int(os.getenv("STYLE_MAX_MEDIA_BYTES", str(100 * 1024**2)))
INSTAGRAM_HOSTS = {"instagram.com", "www.instagram.com", "instagr.am", "www.instagr.am"}
logger = logging.getLogger("nerfstudio-api")

app = FastAPI(
    title="Nerfstudio A100 API",
    version="1.0.0",
    description="Authenticated, asynchronous Nerfstudio reconstruction jobs.",
)
queue: asyncio.Queue[str] = asyncio.Queue()
pipeline_queue: asyncio.Queue[str] = asyncio.Queue()
style_queue: asyncio.Queue[str] = asyncio.Queue()
state_lock = threading.Lock()
worker_task: asyncio.Task | None = None
pipeline_worker_task: asyncio.Task | None = None
style_worker_task: asyncio.Task | None = None


class Job(BaseModel):
    id: str
    status: Literal["queued", "processing", "training", "exporting", "complete", "failed"]
    method: str
    iterations: int
    output_type: str
    created_at: str
    updated_at: str
    error: str | None = None
    result_ready: bool = False


class PipelineJob(BaseModel):
    id: str
    status: Literal[
        "queued",
        "generating_views",
        "stabilizing_views",
        "verifying_views",
        "preparing_dataset",
        "training",
        "complete",
        "failed",
    ]
    prompt: str
    view_count: int
    generated_views: int
    current_angle: float | None = None
    angles: list[float]
    elevations: list[float] = Field(default_factory=list)
    method: str
    iterations: int
    created_at: str
    updated_at: str
    error: str | None = None
    nerfstudio_job_id: str | None = None
    nerfstudio_status: str | None = None
    result_ready: bool = False
    view_urls: list[str] = Field(default_factory=list)
    coherence_score: float | None = None
    verification_notes: list[str] = Field(default_factory=list)
    stabilization_passes: int = 0
    stabilized_views: int = 0
    directive_generated: bool = False
    user_guidance: str = ""


class StyleJob(BaseModel):
    id: str
    status: Literal[
        "queued",
        "downloading_media",
        "selecting_garment",
        "uploading_assets",
        "generating_tryon",
        "complete",
        "approved",
        "failed",
    ]
    created_at: str
    updated_at: str
    instagram_url: str = ""
    garment_category: str = "auto"
    garment_description: str = ""
    source_type: str = ""
    selected_frame: str | None = None
    result_ready: bool = False
    pipeline_id: str | None = None
    error: str | None = None
    provider_requested: str = "auto"
    provider_used: str = ""
    gender_mode: Literal["female", "male"] = "female"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def job_dir(job_id: str) -> Path:
    try:
        parsed = uuid.UUID(job_id)
    except ValueError as exc:
        raise HTTPException(404, "Job not found") from exc
    path = DATA_ROOT / str(parsed)
    if not path.is_dir():
        raise HTTPException(404, "Job not found")
    return path


def read_job(job_id: str) -> dict:
    path = job_dir(job_id) / "job.json"
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(500, "Job metadata is unavailable") from exc


def write_job(job_id: str, **changes: object) -> dict:
    directory = DATA_ROOT / job_id
    path = directory / "job.json"
    with state_lock:
        data = json.loads(path.read_text())
        data.update(changes)
        data["updated_at"] = now()
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2))
        temporary.replace(path)
    return data


def pipeline_dir(pipeline_id: str) -> Path:
    try:
        parsed = uuid.UUID(pipeline_id)
    except ValueError as exc:
        raise HTTPException(404, "Pipeline not found") from exc
    path = PIPELINE_ROOT / str(parsed)
    if not path.is_dir():
        raise HTTPException(404, "Pipeline not found")
    return path


def read_pipeline(pipeline_id: str) -> dict:
    path = pipeline_dir(pipeline_id) / "pipeline.json"
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(500, "Pipeline metadata is unavailable") from exc


def write_pipeline(pipeline_id: str, **changes: object) -> dict:
    directory = PIPELINE_ROOT / pipeline_id
    path = directory / "pipeline.json"
    with state_lock:
        data = json.loads(path.read_text())
        data.update(changes)
        data["updated_at"] = now()
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2))
        temporary.replace(path)
    return data


def style_dir(style_id: str) -> Path:
    try:
        parsed = uuid.UUID(style_id)
    except ValueError as exc:
        raise HTTPException(404, "Style job not found") from exc
    path = STYLE_ROOT / str(parsed)
    if not path.is_dir():
        raise HTTPException(404, "Style job not found")
    return path


def read_style(style_id: str) -> dict:
    try:
        return json.loads((style_dir(style_id) / "style.json").read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(500, "Style job metadata is unavailable") from exc


def write_style(style_id: str, **changes: object) -> dict:
    path = STYLE_ROOT / style_id / "style.json"
    with state_lock:
        data = json.loads(path.read_text())
        data.update(changes)
        data["updated_at"] = now()
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2))
        temporary.replace(path)
    return data


def public_style(style_id: str) -> dict:
    data = read_style(style_id)
    data["garment_url"] = (
        f"/v1/style-jobs/{style_id}/garment" if data.get("selected_frame") else None
    )
    data["result_url"] = (
        f"/v1/style-jobs/{style_id}/result" if data.get("result_ready") else None
    )
    return data


def synced_pipeline(pipeline_id: str) -> dict:
    data = read_pipeline(pipeline_id)
    child_id = data.get("nerfstudio_job_id")
    if child_id:
        child_path = DATA_ROOT / child_id / "job.json"
        if child_path.is_file():
            child = json.loads(child_path.read_text())
            changes: dict[str, object] = {"nerfstudio_status": child["status"]}
            if child["status"] == "complete":
                changes.update(status="complete", result_ready=True)
            elif child["status"] == "failed":
                changes.update(status="failed", error=child.get("error"), result_ready=False)
            elif data["status"] not in {"failed", "complete"}:
                changes.update(status="training")
            if any(data.get(key) != value for key, value in changes.items()):
                data = write_pipeline(pipeline_id, **changes)
    data["view_urls"] = [
        f"/v1/pipelines/{pipeline_id}/views/{index}"
        for index in range(data.get("generated_views", 0))
    ]
    return data


def authenticate(authorization: Annotated[str | None, Header()] = None) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required")
    supplied = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(supplied, API_KEY):
        raise HTTPException(403, "Invalid bearer token")


def safe_extract(archive_path: Path, destination: Path) -> None:
    destination_resolved = destination.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            member = Path(info.filename)
            if member.is_absolute() or ".." in member.parts:
                raise ValueError("ZIP contains an unsafe path")
            # Reject Unix symlinks.
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("ZIP symlinks are not allowed")
            target = (destination / member).resolve()
            if destination_resolved not in target.parents and target != destination_resolved:
                raise ValueError("ZIP contains an unsafe path")
        archive.extractall(destination)


def run_command(job_id: str, args: list[str]) -> None:
    directory = DATA_ROOT / job_id
    log_path = directory / "job.log"
    docker_args = [
        "docker",
        "run",
        "--gpus",
        "all",
        "--rm",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--shm-size=12gb",
        "-e",
        "QT_QPA_PLATFORM=offscreen",
        "-e",
        "HOME=/home/user",
        "-e",
        "USER=nerfstudio-api",
        "-e",
        "LOGNAME=nerfstudio-api",
        "-v",
        f"{directory}:/workspace",
        "-v",
        f"{CACHE_ROOT}:/home/user/.cache",
        NERFSTUDIO_IMAGE,
        *args,
    ]
    with log_path.open("a", encoding="utf-8") as log:
        log.write("\n$ " + " ".join(args) + "\n")
        log.flush()
        result = subprocess.run(
            docker_args,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    if result.returncode:
        raise RuntimeError(f"Command failed with exit code {result.returncode}")


def nano_banana_generate(
    prompt: str,
    images: list[tuple[bytes, str]],
    model: str,
    aspect_ratio: str,
    image_size: str,
) -> tuple[list[tuple[bytes, str]], str]:
    client = genai.Client(
        vertexai=True,
        project=NANO_BANANA_PROJECT,
        location=NANO_BANANA_LOCATION,
        http_options=genai_types.HttpOptions(api_version="v1", timeout=120_000),
    )
    contents: list[object] = [
        genai_types.Part.from_bytes(data=data, mime_type=mime_type)
        for data, mime_type in images
    ]
    contents.append(prompt)
    try:
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=genai_types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
                image_config=genai_types.ImageConfig(
                    aspect_ratio=aspect_ratio,
                    image_size=image_size,
                ),
            ),
        )
    finally:
        client.close()

    output_images: list[tuple[bytes, str]] = []
    output_text: list[str] = []
    candidates = response.candidates or []
    if not candidates or not candidates[0].content:
        raise RuntimeError("Nano Banana returned no content; the request may have been safety-blocked")
    for part in candidates[0].content.parts or []:
        if part.text:
            output_text.append(part.text)
        if part.inline_data and part.inline_data.data:
            data = part.inline_data.data
            if isinstance(data, str):
                data = base64.b64decode(data)
            output_images.append((data, part.inline_data.mime_type or "image/png"))
    if not output_images:
        raise RuntimeError("Nano Banana returned no image; revise the prompt or input images")
    return output_images, "\n".join(output_text).strip()


def nano_response(
    images: list[tuple[bytes, str]],
    text_output: str,
    model: str,
    response_format: str,
) -> Response:
    if response_format == "json":
        return JSONResponse(
            {
                "model": model,
                "text": text_output,
                "images": [
                    {"mime_type": mime_type, "data_base64": base64.b64encode(data).decode("ascii")}
                    for data, mime_type in images
                ],
            }
        )
    data, mime_type = images[0]
    extension = {"image/jpeg": "jpg", "image/webp": "webp"}.get(mime_type, "png")
    return Response(
        content=data,
        media_type=mime_type,
        headers={
            "Content-Disposition": f'attachment; filename="nano-banana.{extension}"',
            "X-Nano-Banana-Model": model,
            "X-Generated-Images": str(len(images)),
        },
    )


def validate_nano_options(
    prompt: str,
    model: str,
    aspect_ratio: str,
    image_size: str,
    response_format: str,
) -> None:
    if not prompt.strip() or len(prompt) > 20_000:
        raise HTTPException(422, "prompt must contain between 1 and 20,000 characters")
    if model not in NANO_BANANA_MODELS:
        raise HTTPException(422, f"model must be one of {sorted(NANO_BANANA_MODELS)}")
    if aspect_ratio not in NANO_ASPECT_RATIOS:
        raise HTTPException(422, f"aspect_ratio must be one of {sorted(NANO_ASPECT_RATIOS)}")
    if image_size not in NANO_IMAGE_SIZES:
        raise HTTPException(422, f"image_size must be one of {sorted(NANO_IMAGE_SIZES)}")
    if response_format not in {"image", "json"}:
        raise HTTPException(422, "response_format must be image or json")


def append_pipeline_log(pipeline_id: str, message: str) -> None:
    path = PIPELINE_ROOT / pipeline_id / "pipeline.log"
    with path.open("a", encoding="utf-8") as log:
        log.write(f"[{now()}] {message}\n")


def response_image(response: object) -> tuple[bytes, str]:
    candidates = getattr(response, "candidates", None) or []
    if not candidates or not candidates[0].content:
        raise RuntimeError("Image model returned no content")
    for part in candidates[0].content.parts or []:
        if part.inline_data and part.inline_data.data:
            data = part.inline_data.data
            if isinstance(data, str):
                data = base64.b64decode(data)
            return data, part.inline_data.mime_type or "image/png"
    raise RuntimeError("Image model returned no image")


def canonical_view_prompt(user_prompt: str) -> str:
    return f"""
Create exactly one canonical photorealistic object image for a 3D reconstruction pipeline.

SUBJECT INTENT:
{user_prompt}

- Merge the supplied references into one exact subject. Preserve geometry, proportions, materials, colors,
  markings, seams, wear, accessories, identity, clothing, and every distinguishing detail.
- Keep the subject in one neutral rigid pose. Do not add, remove, redesign, mirror, bend, open, or hide parts.
- Show the complete subject in a front three-quarter view, centered with 15 percent margin.
- Fixed 50 mm lens, 10 degree camera elevation, uniform light-gray seamless background, soft fixed studio light.
- One subject only. No text, labels, frames, grid, collage, props, people, floor pattern, or extra objects.
- Output one square image. This will be the locked first and last frame of a continuous camera orbit.
""".strip()


def generate_subject_directive(
    references: list[tuple[bytes, str]],
    user_guidance: str,
) -> str:
    """Derive a reconstruction-safe immutable subject contract from images."""
    client = genai.Client(
        vertexai=True,
        project=NANO_BANANA_PROJECT,
        location=NANO_BANANA_LOCATION,
        http_options=genai_types.HttpOptions(api_version="v1", timeout=120_000),
    )
    contents: list[object] = [
        """Inspect every supplied reference and write one precise subject directive for a metric 3D reconstruction
pipeline. Return only the directive as a compact paragraph, approximately 80 to 160 words.

Identify the single intended subject and specify its immutable identity, geometry, proportions, materials, colors,
markings, clothing, accessories, surface wear, and spatial relationships. Define one frozen pose visible in the
references. For a person, explicitly lock head direction relative to shoulders and chest, eye direction, expression,
spine, arms, elbows, wrists, hands, fingers, stance, garment folds, and accessories. For an object, lock every joint,
hinge, dial, seam, panel, and movable part. Resolve multiple references as views of the same subject. Do not describe
camera motion, angle changes, background, a turntable, or the reconstruction process.

Optional user guidance is authoritative for requested additions or changes, but do not invent any other changes.
Optional user guidance: """ + (user_guidance.strip() or "none")
    ]
    for index, (data, mime_type) in enumerate(references):
        contents.append(f"Reference image {index + 1}:")
        contents.append(genai_types.Part.from_bytes(data=data, mime_type=mime_type))
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents,
            config=genai_types.GenerateContentConfig(temperature=0.1),
        )
    finally:
        client.close()
    directive = (response.text or "").strip()
    if not directive:
        raise RuntimeError("The reference analyzer returned no subject directive")
    return directive[:4000]


def load_youcam_api_key() -> str:
    configured = os.getenv("YOUCAM_API_KEY", "").strip()
    if configured:
        return configured
    if not YOUCAM_API_KEY_FILE.is_file():
        return ""
    match = re.search(r"\bsk-[A-Za-z0-9_-]{20,}\b", YOUCAM_API_KEY_FILE.read_text())
    return match.group(0) if match else ""


def clothing_provider_specs() -> dict[str, dict]:
    """Return public-safe provider capabilities without exposing credentials.

    Additional vendors are connected through a tiny webhook contract. This keeps
    vendor payloads out of the product UI and lets deployments add or remove
    clothing engines without changing application code.
    """
    specs: dict[str, dict] = {
        "youcam": {
            "id": "youcam",
            "label": "YouCam Clothes v3",
            "kind": "native",
            "configured": bool(load_youcam_api_key()),
        }
    }
    try:
        configured = json.loads(CLOTHING_WEBHOOK_PROVIDERS_JSON)
    except json.JSONDecodeError:
        logger.warning("CLOTHING_WEBHOOK_PROVIDERS_JSON is invalid; ignoring webhook providers")
        configured = []
    if not isinstance(configured, list):
        configured = []
    for item in configured:
        if not isinstance(item, dict):
            continue
        provider_id = re.sub(r"[^a-z0-9_-]", "", str(item.get("id", "")).lower())
        endpoint = str(item.get("endpoint", "")).strip()
        token_env = str(item.get("token_env", "")).strip()
        if not provider_id or not endpoint.startswith("https://") or not token_env:
            continue
        try:
            timeout = min(900, max(30, int(item.get("timeout", YOUCAM_TASK_TIMEOUT))))
        except (TypeError, ValueError):
            timeout = YOUCAM_TASK_TIMEOUT
        specs[provider_id] = {
            "id": provider_id,
            "label": str(item.get("label") or provider_id.replace("-", " ").title())[:80],
            "kind": "webhook",
            "configured": bool(os.getenv(token_env, "").strip()),
            "endpoint": endpoint,
            "token_env": token_env,
            "timeout": timeout,
        }
    return specs


def public_clothing_providers() -> list[dict]:
    specs = clothing_provider_specs()
    ordered_ids = list(dict.fromkeys(CLOTHING_PROVIDER_ORDER + list(specs)))
    providers = [
        {
            "id": provider_id,
            "label": specs[provider_id]["label"],
            "kind": specs[provider_id]["kind"],
            "configured": specs[provider_id]["configured"],
        }
        for provider_id in ordered_ids
        if provider_id in specs
    ]
    return [
        {
            "id": "auto",
            "label": "Auto · best available",
            "kind": "router",
            "configured": any(provider["configured"] for provider in providers),
        },
        *providers,
    ]


def resolve_clothing_provider(requested: str) -> dict:
    specs = clothing_provider_specs()
    if requested != "auto":
        selected = specs.get(requested)
        if not selected:
            raise RuntimeError(f"Unknown clothing provider: {requested}")
        if not selected["configured"]:
            raise RuntimeError(f"{selected['label']} is not configured")
        return selected
    for provider_id in CLOTHING_PROVIDER_ORDER:
        selected = specs.get(provider_id)
        if selected and selected["configured"]:
            return selected
    for selected in specs.values():
        if selected["configured"]:
            return selected
    raise RuntimeError("No clothing provider is configured")


def clothing_provider_candidates(requested: str) -> list[dict]:
    if requested != "auto":
        return [resolve_clothing_provider(requested)]
    specs = clothing_provider_specs()
    ordered_ids = list(dict.fromkeys(CLOTHING_PROVIDER_ORDER + list(specs)))
    candidates = [
        specs[provider_id]
        for provider_id in ordered_ids
        if provider_id in specs and specs[provider_id]["configured"]
    ]
    if not candidates:
        raise RuntimeError("No clothing provider is configured")
    return candidates


def append_style_log(style_id: str, message: str) -> None:
    with (STYLE_ROOT / style_id / "style.log").open("a", encoding="utf-8") as log:
        log.write(f"[{now()}] {message}\n")


def validate_instagram_url(url: str) -> str:
    cleaned = url.strip()
    parsed = urllib.parse.urlparse(cleaned)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in INSTAGRAM_HOSTS:
        raise ValueError("Use a public https://instagram.com reel or post URL")
    if not re.match(r"^/(reel|reels|p)/", parsed.path):
        raise ValueError("The Instagram URL must point to a reel or post")
    return cleaned


def normalize_image_to_jpeg(source: Path | bytes, destination: Path) -> None:
    payload: Path | io.BytesIO = source if isinstance(source, Path) else io.BytesIO(source)
    with Image.open(payload) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
        width, height = image.size
        if min(width, height) < 384:
            scale = 384 / min(width, height)
            image = image.resize(
                (round(width * scale), round(height * scale)), Image.Resampling.LANCZOS
            )
        image.save(destination, "JPEG", quality=92, optimize=True)
    if destination.stat().st_size >= 10 * 1024**2:
        raise RuntimeError("Prepared image exceeds YouCam's 10 MB limit")


def download_instagram_media(url: str, destination: Path) -> list[Path]:
    validated = validate_instagram_url(url)
    destination.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    try:
        from yt_dlp import YoutubeDL

        with YoutubeDL(
            {
                "outtmpl": str(destination / "instagram-%(id)s-%(autonumber)02d.%(ext)s"),
                "format": "best[height<=1080]/best",
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "socket_timeout": 30,
                "retries": 2,
            }
        ) as downloader:
            downloader.download([validated])
    except Exception as exc:
        errors.append(str(exc)[:180])

    supported = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".mkv", ".webm"}
    media = [path for path in destination.rglob("*") if path.is_file() and path.suffix.lower() in supported]
    if not media:
        try:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "gallery_dl",
                    "--directory",
                    str(destination),
                    "--no-mtime",
                    validated,
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=180,
            )
        except Exception as exc:
            errors.append(str(exc)[:180])
        media = [path for path in destination.rglob("*") if path.is_file() and path.suffix.lower() in supported]
    if not media:
        detail = "; ".join(errors) or "no downloadable media found"
        raise RuntimeError(
            "Instagram did not expose public media. Upload a garment image instead. " + detail
        )
    if sum(path.stat().st_size for path in media) > STYLE_MAX_MEDIA_BYTES:
        raise RuntimeError("Instagram media exceeds the 100 MB safety limit")
    return sorted(media)[:12]


def sample_video_frames(video: Path, destination: Path, start_index: int) -> list[Path]:
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    duration = max(0.1, float(probe.stdout.strip()))
    frame_count = min(10, max(4, math.ceil(duration / 2)))
    frames: list[Path] = []
    for offset in range(frame_count):
        timestamp = duration * (offset + 0.5) / frame_count
        output = destination / f"candidate-{start_index + offset:02d}.jpg"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-vf",
                "scale='min(1280,iw)':-2",
                "-q:v",
                "2",
                "-y",
                str(output),
            ],
            check=True,
            timeout=45,
        )
        frames.append(output)
    return frames


def collect_garment_candidates(media: list[Path], directory: Path) -> list[Path]:
    candidates_dir = directory / "candidates"
    candidates_dir.mkdir(exist_ok=True)
    images = {".jpg", ".jpeg", ".png", ".webp"}
    videos = {".mp4", ".mov", ".mkv", ".webm"}
    candidates: list[Path] = []
    for path in media:
        if len(candidates) >= 12:
            break
        if path.suffix.lower() in images:
            output = candidates_dir / f"candidate-{len(candidates):02d}.jpg"
            normalize_image_to_jpeg(path, output)
            candidates.append(output)
        elif path.suffix.lower() in videos:
            candidates.extend(sample_video_frames(path, candidates_dir, len(candidates)))
            candidates = candidates[:12]
    if not candidates:
        raise RuntimeError("No usable garment frames were found in the Instagram media")
    return candidates


def analyze_garment_candidates(candidates: list[Path], requested_category: str) -> tuple[Path, str, str]:
    client = genai.Client(
        vertexai=True,
        project=NANO_BANANA_PROJECT,
        location=NANO_BANANA_LOCATION,
        http_options=genai_types.HttpOptions(api_version="v1", timeout=120_000),
    )
    contents: list[object] = [
        """You are selecting an outfit reference for a high-fidelity virtual try-on API. Inspect all candidate
frames and choose the single frame where the clothing is most complete, front-facing, sharp, unoccluded, and
least distorted. Prefer a standing subject and ensure the entire requested garment region is visible. Then write
a precise visual description of the garment only: category, silhouette, layers, fabric, colors, pattern, seams,
closures, neckline, sleeve/leg length, fit, logos and accessories. Do not describe the person's identity or face.

Return strict JSON with keys selected_index (zero-based integer), garment_category (one of upper_body,
lower_body, full_body), and description (60-130 words). Requested category: """ + requested_category
    ]
    for index, path in enumerate(candidates):
        contents.append(f"Candidate {index}:")
        contents.append(
            genai_types.Part.from_bytes(data=path.read_bytes(), mime_type="image/jpeg")
        )
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents,
            config=genai_types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
            ),
        )
    finally:
        client.close()
    raw = (response.text or "").strip()
    try:
        analysis = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("The garment analyzer returned invalid metadata") from exc
    selected_index = int(analysis.get("selected_index", 0))
    if not 0 <= selected_index < len(candidates):
        selected_index = 0
    category = str(analysis.get("garment_category", "full_body"))
    if requested_category != "auto":
        category = requested_category
    if category not in {"upper_body", "lower_body", "full_body"}:
        category = "full_body"
    description = str(analysis.get("description", "")).strip()
    if not description:
        raise RuntimeError("The garment analyzer returned no description")
    return candidates[selected_index], description[:2000], category


def youcam_json(method: str, path: str, payload: dict | None = None, timeout: int = 60) -> dict:
    api_key = load_youcam_api_key()
    if not api_key:
        raise RuntimeError("YouCam API credentials are not configured")
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        YOUCAM_API_BASE + path,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            error_body = json.loads(exc.read().decode("utf-8"))
            detail = error_body.get("message") or error_body.get("error") or f"HTTP {exc.code}"
        except Exception:
            detail = f"HTTP {exc.code}"
        raise RuntimeError(f"YouCam request failed: {detail}") from exc
    if parsed.get("status") not in {None, 200}:
        raise RuntimeError(f"YouCam request failed with status {parsed.get('status')}")
    data = parsed.get("data", parsed)
    if not isinstance(data, dict):
        raise RuntimeError("YouCam returned an unexpected response")
    return data


def youcam_upload(path: Path) -> str:
    size = path.stat().st_size
    if size >= 10 * 1024**2:
        raise RuntimeError("YouCam image exceeds 10 MB")
    allocation = youcam_json(
        "POST",
        "/s2s/v2.0/file/cloth-v3",
        {
            "files": [
                {
                    "content_type": "image/jpg",
                    "file_name": path.name,
                    "file_size": size,
                }
            ]
        },
    )
    try:
        remote = allocation["files"][0]
        upload_request = remote["requests"][0]
        file_id = remote["file_id"]
        upload_url = upload_request["url"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("YouCam did not return an upload slot") from exc
    request = urllib.request.Request(
        upload_url,
        data=path.read_bytes(),
        method=upload_request.get("method", "PUT"),
        headers={"Content-Type": "image/jpg", "Content-Length": str(size)},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        if response.status not in {200, 201, 204}:
            raise RuntimeError("YouCam asset upload failed")
    return file_id


def download_remote_image(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "Parallax/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read(10 * 1024**2 + 1)
    if len(data) > 10 * 1024**2:
        raise RuntimeError("YouCam result exceeded 10 MB")
    normalize_image_to_jpeg(data, destination)


def multipart_payload(fields: dict[str, str], files: dict[str, Path]) -> tuple[bytes, str]:
    boundary = f"parallax-{secrets.token_hex(18)}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )
    for name, path in files.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                (
                    f'Content-Disposition: form-data; name="{name}"; '
                    f'filename="{path.name}"\r\n'
                ).encode(),
                b"Content-Type: image/jpeg\r\n\r\n",
                path.read_bytes(),
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def find_provider_result_url(payload: object) -> str:
    if isinstance(payload, str) and payload.startswith("https://"):
        return payload
    if isinstance(payload, dict):
        for key in ("result_url", "output_url", "image_url", "url"):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith("https://"):
                return value
        for key in ("result", "output", "data", "images"):
            found = find_provider_result_url(payload.get(key))
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = find_provider_result_url(item)
            if found:
                return found
    return ""


def run_webhook_clothing_provider(
    spec: dict,
    identity_path: Path,
    garment_path: Path,
    category: str,
    gender_mode: str,
    destination: Path,
) -> None:
    token = os.getenv(spec["token_env"], "").strip()
    body, content_type = multipart_payload(
        {"garment_category": category, "gender_mode": gender_mode},
        {"identity_image": identity_path, "garment_image": garment_path},
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, image/*",
        "Content-Type": content_type,
        "User-Agent": "Parallax/1.0",
    }
    request = urllib.request.Request(spec["endpoint"], data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=spec["timeout"]) as response:
            response_type = response.headers.get_content_type()
            raw = response.read(10 * 1024**2 + 1)
    except urllib.error.HTTPError as exc:
        detail = exc.read(2048).decode("utf-8", errors="replace")
        raise RuntimeError(f"{spec['label']} request failed: HTTP {exc.code} {detail[:240]}") from exc
    if len(raw) > 10 * 1024**2:
        raise RuntimeError(f"{spec['label']} result exceeded 10 MB")
    if response_type.startswith("image/"):
        normalize_image_to_jpeg(raw, destination)
        return
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{spec['label']} returned an unsupported response") from exc

    result_url = find_provider_result_url(payload)
    status_url = str(payload.get("status_url", "")) if isinstance(payload, dict) else ""
    if not result_url and status_url.startswith("https://"):
        deadline = time.monotonic() + spec["timeout"]
        while time.monotonic() < deadline:
            poll_request = urllib.request.Request(
                status_url,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
            with urllib.request.urlopen(poll_request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
            result_url = find_provider_result_url(payload)
            status = str(payload.get("status", "")).lower() if isinstance(payload, dict) else ""
            if result_url:
                break
            if status in {"failed", "error", "cancelled"}:
                raise RuntimeError(f"{spec['label']} virtual try-on failed")
            time.sleep(3)
    if not result_url:
        raise RuntimeError(f"{spec['label']} returned no result image")
    download_remote_image(result_url, destination)


def run_youcam_clothing_provider(
    identity_path: Path, garment_path: Path, category: str, destination: Path
) -> None:
    source_file_id = youcam_upload(identity_path)
    garment_file_id = youcam_upload(garment_path)
    task = youcam_json(
        "POST",
        "/s2s/v2.0/task/cloth-v3",
        {
            "src_file_id": source_file_id,
            "ref_file_id": garment_file_id,
            "garment_category": category,
        },
    )
    task_id = task.get("task_id")
    if not task_id:
        raise RuntimeError("YouCam did not return a task ID")
    deadline = time.monotonic() + YOUCAM_TASK_TIMEOUT
    result_url = ""
    while time.monotonic() < deadline:
        result = youcam_json(
            "GET", f"/s2s/v2.0/task/cloth-v3/{urllib.parse.quote(task_id, safe='')}"
        )
        task_status = result.get("task_status")
        if task_status == "success":
            result_url = str((result.get("results") or {}).get("url") or "")
            break
        if task_status in {"error", "failed"}:
            error = result.get("error") or "virtual try-on failed"
            raise RuntimeError(f"YouCam Clothes v3 failed: {error}")
        time.sleep(3)
    if not result_url:
        raise TimeoutError("YouCam Clothes v3 timed out")
    download_remote_image(result_url, destination)


def execute_style_job(style_id: str) -> None:
    directory = STYLE_ROOT / style_id
    metadata = read_style(style_id)
    try:
        candidates: list[Path]
        fallback = directory / "garment-upload.jpg"
        instagram_url = metadata.get("instagram_url", "")
        if instagram_url:
            write_style(style_id, status="downloading_media")
            append_style_log(style_id, "Downloading public Instagram media")
            try:
                media = download_instagram_media(instagram_url, directory / "instagram")
                candidates = collect_garment_candidates(media, directory)
                source_type = "instagram"
            except Exception:
                if not fallback.is_file():
                    raise
                candidates = [fallback]
                source_type = "uploaded_fallback"
                append_style_log(style_id, "Instagram media unavailable; using uploaded garment fallback")
        else:
            candidates = [fallback]
            source_type = "uploaded"

        write_style(style_id, status="selecting_garment", source_type=source_type)
        selected, description, category = analyze_garment_candidates(
            candidates, metadata.get("garment_category", "auto")
        )
        garment_path = directory / "garment.jpg"
        normalize_image_to_jpeg(selected, garment_path)
        write_style(
            style_id,
            garment_category=category,
            garment_description=description,
            selected_frame=garment_path.name,
        )
        append_style_log(style_id, f"Selected garment reference and classified {category}")

        providers = clothing_provider_candidates(metadata.get("provider_requested", "auto"))
        write_style(style_id, status="uploading_assets")
        last_provider_error: Exception | None = None
        for index, provider in enumerate(providers):
            write_style(style_id, status="generating_tryon", provider_used=provider["id"])
            append_style_log(style_id, f"Routing virtual try-on through {provider['label']}")
            try:
                if provider["kind"] == "native":
                    run_youcam_clothing_provider(
                        directory / "identity.jpg", garment_path, category, directory / "result.jpg"
                    )
                else:
                    run_webhook_clothing_provider(
                        provider,
                        directory / "identity.jpg",
                        garment_path,
                        category,
                        metadata.get("gender_mode", "female"),
                        directory / "result.jpg",
                    )
                last_provider_error = None
                break
            except Exception as exc:
                last_provider_error = exc
                if metadata.get("provider_requested", "auto") != "auto" or index == len(providers) - 1:
                    raise
                append_style_log(
                    style_id,
                    f"{provider['label']} was unavailable; trying the next configured fit engine",
                )
        if last_provider_error is not None:
            raise last_provider_error
        write_style(style_id, status="complete", result_ready=True)
        append_style_log(style_id, "Virtual try-on preview is ready for approval")
    except Exception as exc:
        logger.exception("Style job failed")
        write_style(style_id, status="failed", error=str(exc)[:500], result_ready=False)
        append_style_log(style_id, f"Style job failed: {str(exc)[:500]}")


def pose_visibility_constraint(angle: float) -> str:
    """Describe facial visibility implied by a rigid camera orbit.

    Explicit visibility constraints prevent an image editor from keeping a
    person's face aimed at the camera while the body advances around the orbit.
    """
    folded = min(angle % 360.0, 360.0 - (angle % 360.0))
    if folded <= 15.0:
        return "exact frontal view: face and chest point at camera; facial features are centered"
    if folded <= 45.0:
        return "front three-quarter view: both eyes may remain visible; head and chest share the same rotation"
    if folded <= 75.0:
        return "near-profile view: at most one full eye is visible; nose and chest axes remain parallel"
    if folded <= 105.0:
        return "strict side profile: exactly one eye-side profile; head is not turned toward the camera"
    if folded <= 135.0:
        return "rear three-quarter view: face is mostly occluded and no far eye is visible"
    if folded <= 165.0:
        return "near-rear view: show the back/side of the skull; no eye or frontal nose surface is visible"
    return "exact rear view: show only the back of the head and body; no face, eye, nose, or looking-back pose"


def save_normalized_view(data: bytes, path: Path, dimensions: tuple[int, int] | None) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as source:
        image = source.convert("RGB")
        if dimensions and image.size != dimensions:
            image = image.resize(dimensions, Image.Resampling.LANCZOS)
        image.save(path, "PNG", optimize=True)
        return image.size


def convert_rgb_splat_to_sh(path: Path) -> None:
    """Convert Nerfstudio's stable RGB splat export to viewer-compatible DC SH fields."""
    payload = path.read_bytes()
    marker = b"end_header\n"
    header_end = payload.index(marker) + len(marker)
    header = payload[:header_end].decode("ascii")
    required = "property uchar red\nproperty uchar green\nproperty uchar blue\n"
    if required not in header:
        raise RuntimeError("RGB splat export has an unexpected PLY schema")
    vertex_line = next(line for line in header.splitlines() if line.startswith("element vertex "))
    vertex_count = int(vertex_line.rsplit(" ", 1)[-1])
    source_record = struct.Struct("<6f3B8f")
    target_record = struct.Struct("<17f")
    source = memoryview(payload)[header_end:]
    if len(source) != vertex_count * source_record.size:
        raise RuntimeError("RGB splat export has an unexpected binary length")
    output_header = header.replace(
        required,
        "property float f_dc_0\nproperty float f_dc_1\nproperty float f_dc_2\n",
    ).encode("ascii")
    output = bytearray(output_header)
    sh_dc = 0.28209479177387814
    for offset in range(0, len(source), source_record.size):
        values = source_record.unpack_from(source, offset)
        red, green, blue = values[6:9]
        dc = tuple((channel / 255.0 - 0.5) / sh_dc for channel in (red, green, blue))
        output.extend(target_record.pack(*values[:6], *dc, *values[9:]))
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(output)
    temporary.replace(path)


def extract_half_orbit_frames(video: Path, destination: Path) -> list[Path]:
    """Sample six frames from a constrained eight-second 180-degree orbit."""
    destination.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video),
        "-vf",
        "crop=ih:ih:(iw-ih)/2:0,fps=0.75",
        "-frames:v",
        str(PIPELINE_HALF_VIEW_COUNT),
        str(destination / "%03d.png"),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)
    if completed.returncode != 0:
        raise RuntimeError(f"Veo frame extraction failed: {completed.stderr[-400:]}")
    for index in range(PIPELINE_HALF_VIEW_COUNT):
        source = destination / f"{index + 1:03d}.png"
        target = destination / f"{index:03d}.png"
        if not source.is_file():
            raise RuntimeError("Veo returned fewer than six usable half-orbit frames")
        source.replace(target)
    return [destination / f"{index:03d}.png" for index in range(PIPELINE_HALF_VIEW_COUNT)]


def verify_turntable(
    client: genai.Client,
    view_files: list[Path],
    angles: list[float],
    user_prompt: str,
) -> dict:
    contents: list[object] = [
        """Audit this turntable sequence for metric 3D reconstruction, not merely visual appeal. Compare immutable
subject identity, geometry, materials, markings, pose, scale, lighting, and framing. Confirm that views advance
clockwise through the supplied target azimuths without repeats, pauses, reversals, or skipped arcs. Estimate each
actual azimuth relative to view 0 as an unwrapped, monotonically increasing number from 0 toward 360. Return strict
JSON: {\"score\": number from 0 to 1, \"identity_score\": number from 0 to 1, \"pose_score\": number from 0 to 1,
\"estimated_azimuths\": [number for every view], \"issues\": [{\"view\": zero-based integer, \"reason\": string}],
\"notes\": [string]}. A repeated angle or target error over 20 degrees is a reconstruction failure. Normal
perspective and occlusion changes are expected.

POSE MUST BE MEASURED IN THE SUBJECT'S BODY-LOCAL COORDINATE SYSTEM, never in image coordinates. Do not call the
expected visibility change caused by camera motion a pose change. For a rigid front-facing person, a correct orbit
shows the full face near 0 degrees, a profile near 90, only the back of the head near 180, the opposite profile near
270, and the face again near 360. That is fixed pose, not head movement. Penalize pose only when the head-to-chest,
head-to-shoulder, limb-to-torso, hand, expression, or garment-fold relationship changes between views—for example,
looking back over a shoulder while the torso faces away. Compare the nose axis against the chest normal and shoulder
line within each view. The intended subject is: """ + user_prompt
    ]
    for index, (path, angle) in enumerate(zip(view_files, angles)):
        contents.append(f"View {index}, azimuth {angle:.1f} degrees:")
        contents.append(genai_types.Part.from_bytes(data=path.read_bytes(), mime_type="image/png"))
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=contents,
        config=genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.0,
        ),
    )
    try:
        result = json.loads(response.text or "{}")
    except json.JSONDecodeError:
        return {
            "score": 0.0,
            "identity_score": 0.0,
            "pose_score": 0.0,
            "estimated_azimuths": None,
            "issues": [],
            "notes": ["Verifier returned an unreadable response"],
        }
    score = max(0.0, min(1.0, float(result.get("score", 0.0))))
    identity_score = max(0.0, min(1.0, float(result.get("identity_score", score))))
    pose_score = max(0.0, min(1.0, float(result.get("pose_score", score))))
    estimated = result.get("estimated_azimuths")
    if isinstance(estimated, list) and len(estimated) == len(angles):
        try:
            estimated = [float(value) for value in estimated]
            errors = [abs(actual - target) for actual, target in zip(estimated, angles)]
            steps = [right - left for left, right in zip(estimated, estimated[1:])]
            measured_pose_score = max(0.0, 1.0 - sum(errors) / len(errors) / 35.0)
            if any(step < 12.0 or step > 48.0 for step in steps):
                measured_pose_score = min(measured_pose_score, 0.65)
            pose_score = min(pose_score, measured_pose_score)
        except (TypeError, ValueError):
            estimated = None
    else:
        estimated = None
        pose_score = min(pose_score, 0.5)
    score = min(score, identity_score, pose_score)
    issues = result.get("issues") if isinstance(result.get("issues"), list) else []
    notes = result.get("notes") if isinstance(result.get("notes"), list) else []
    return {
        "score": score,
        "identity_score": identity_score,
        "pose_score": pose_score,
        "estimated_azimuths": estimated,
        "issues": issues,
        "notes": [str(note)[:300] for note in notes[:8]],
    }


def verifier_issue_indices(verification: dict, view_count: int) -> list[int]:
    indices: list[int] = []
    for issue in verification.get("issues", []):
        try:
            candidate = int(issue.get("view", -1))
        except (AttributeError, TypeError, ValueError):
            continue
        if 0 <= candidate < view_count and candidate not in indices:
            indices.append(candidate)
    return indices


def reconstruction_views_usable(verification: dict) -> bool:
    """Allow identity-stable, complete orbits through with a quality warning.

    Hunyuan can still reconstruct a useful subject when articulated pose is
    imperfect. Identity loss or missing camera coverage are hard blockers; a
    low pose/coherence score is advisory after stabilization has been tried.
    """
    estimated = verification.get("estimated_azimuths")
    return (
        float(verification.get("identity_score", 0.0)) >= PIPELINE_MIN_IDENTITY_SCORE
        and isinstance(estimated, list)
        and len(estimated) >= 10
    )


def record_soft_coherence_warning(pipeline_id: str, verification: dict) -> None:
    score = float(verification.get("score", 0.0))
    warning = (
        f"Quality warning: coherence {score:.2f} is below the preferred "
        f"{PIPELINE_COHERENCE_THRESHOLD:.2f}; continuing reconstruction because identity "
        "and camera coverage remain usable."
    )
    verification.setdefault("notes", []).insert(0, warning)
    append_pipeline_log(pipeline_id, warning)


def stabilize_turntable_views(
    client: genai.Client,
    pipeline_id: str,
    view_files: list[Path],
    angles: list[float],
    canonical: Path,
    rear: Path,
    user_prompt: str,
    verification: dict,
) -> dict:
    """Repair articulated pose drift while retaining the measured camera orbit.

    Video generators can satisfy an orbit request by turning a person or an
    articulated object inside the shot.  This pass treats the canonical image as
    the immutable pose contract and the video frame only as evidence for camera
    angle, occlusion, and hidden-side appearance.  Every accepted pass is audited
    again; this function never weakens the reconstruction gate.
    """
    if PIPELINE_STABILIZATION_PASSES <= 0:
        return verification

    directory = pipeline_dir(pipeline_id)
    stabilization_root = directory / "stabilization"
    stabilization_root.mkdir(exist_ok=True)
    accepted_verification = verification
    pipeline_metadata = read_pipeline(pipeline_id)
    accepted_passes = int(pipeline_metadata.get("stabilization_passes", 0))
    stabilized_views = int(pipeline_metadata.get("stabilized_views", 0))

    for pass_number in range(accepted_passes + 1, PIPELINE_STABILIZATION_PASSES + 1):
        if accepted_verification["score"] >= PIPELINE_COHERENCE_THRESHOLD:
            break
        issue_indices = verifier_issue_indices(accepted_verification, len(view_files))
        # A low sequence-wide pose score usually means the actor moved in most
        # frames. Repair the full orbit on the first pass, then focus any second
        # pass on the verifier's remaining outliers.
        targets = list(range(len(view_files))) if pass_number == 1 else issue_indices
        if not targets:
            targets = list(range(len(view_files)))

        pass_dir = stabilization_root / f"pass-{pass_number:02d}"
        if pass_dir.exists():
            shutil.rmtree(pass_dir)
        pass_dir.mkdir()
        for index, source in enumerate(view_files):
            shutil.copy2(source, pass_dir / f"{index:03d}.png")

        write_pipeline(
            pipeline_id,
            status="stabilizing_views",
            stabilization_passes=accepted_passes,
            stabilized_views=stabilized_views,
        )
        append_pipeline_log(
            pipeline_id,
            f"Pose stabilizer pass {pass_number}: repairing {len(targets)} verifier-selected "
            f"view(s) across {min(PIPELINE_STABILIZATION_WORKERS, len(targets))} workers",
        )
        canonical_payload = canonical.read_bytes()
        rear_payload = rear.read_bytes()

        def repair_view(index: int) -> tuple[int, bytes | None, tuple[int, int] | None, str | None]:
            source = view_files[index]
            angle = angles[index]
            worker_client = genai.Client(
                vertexai=True,
                project=NANO_BANANA_PROJECT,
                location=NANO_BANANA_LOCATION,
                http_options=genai_types.HttpOptions(api_version="v1", timeout=300_000),
            )
            try:
                response = worker_client.models.generate_content(
                    model=NANO_BANANA_STABILIZER_MODEL,
                    contents=[
                        "MASTER POSE AND IDENTITY (azimuth 0 degrees):",
                        genai_types.Part.from_bytes(data=canonical_payload, mime_type="image/png"),
                        "REAR APPEARANCE REFERENCE (use only for clothing/material details; its pose may be wrong):",
                        genai_types.Part.from_bytes(data=rear_payload, mime_type="image/png"),
                        f"RAW CAMERA-VIEW EVIDENCE (target azimuth {angle:.1f} degrees):",
                        genai_types.Part.from_bytes(data=source.read_bytes(), mime_type="image/png"),
                        f"""Create one corrected photogrammetry frame of this exact subject: {user_prompt}

The camera is at exactly {angle:.1f} degrees clockwise around the vertical axis, with the same 50 mm lens,
10 degree elevation, distance, scale, crop, background, and studio lighting as the raw camera-view evidence.

POSE LOCK IS THE HIGHEST PRIORITY. The subject is a rigid statue frozen at the instant shown in MASTER POSE AND
IDENTITY. Preserve the exact head-to-shoulder direction, eye direction, facial expression, spine, shoulder line,
arm, elbow, wrist, hand and finger placement, stance, garment folds, accessories, and silhouette. These body-local
relationships must not change at another azimuth. Do not make the subject look toward the camera, turn its head,
twist its torso, move a limb, change expression, mirror the body, or re-pose clothing. Only the external camera
moves. Use RAW CAMERA-VIEW EVIDENCE for viewpoint and occlusion, never for an altered pose. Use REAR APPEARANCE
REFERENCE only to preserve details that the master image cannot show; ignore any head turn or pose in it.

REQUIRED VISIBILITY AT {angle:.1f} DEGREES: {pose_visibility_constraint(angle)}. The nose axis and chest normal
remain parallel throughout the orbit. This geometric visibility rule overrides conflicting face visibility in either
reference image and is mandatory evidence that the person did not follow the camera with their head.

Return exactly one square corrected image. No text, comparison panel, border, grid, extra subject, or explanation.""",
                    ],
                    config=genai_types.GenerateContentConfig(
                        response_modalities=["TEXT", "IMAGE"],
                        image_config=genai_types.ImageConfig(aspect_ratio="1:1", image_size="1K"),
                        temperature=0.0,
                    ),
                )
                repaired_bytes, _ = response_image(response)
                with Image.open(source) as opened:
                    dimensions = opened.size
                return index, repaired_bytes, dimensions, None
            except Exception as exc:
                return index, None, None, str(exc)[:240]
            finally:
                worker_client.close()

        repaired_count = 0
        with ThreadPoolExecutor(
            max_workers=min(PIPELINE_STABILIZATION_WORKERS, len(targets)),
            thread_name_prefix="pose-stabilizer",
        ) as executor:
            repairs = executor.map(repair_view, targets)
            for index, repaired_bytes, dimensions, error in repairs:
                angle = angles[index]
                if repaired_bytes is not None and dimensions is not None:
                    save_normalized_view(repaired_bytes, pass_dir / f"{index:03d}.png", dimensions)
                    repaired_count += 1
                    append_pipeline_log(
                        pipeline_id,
                        f"Pose stabilizer corrected view {index} at {angle:.1f} degrees",
                    )
                else:
                    append_pipeline_log(
                        pipeline_id,
                        f"Pose stabilizer kept original view {index}: {error or 'no image returned'}",
                    )

        if repaired_count == 0:
            append_pipeline_log(pipeline_id, f"Pose stabilizer pass {pass_number} produced no repairs")
            break

        candidate_files = [pass_dir / f"{index:03d}.png" for index in range(len(view_files))]
        write_pipeline(pipeline_id, status="verifying_views")
        append_pipeline_log(pipeline_id, f"Auditing pose stabilizer pass {pass_number}")
        candidate_verification = verify_turntable(client, candidate_files, angles, user_prompt)
        old_quality = (
            accepted_verification["score"],
            accepted_verification["pose_score"],
            accepted_verification["identity_score"],
        )
        new_quality = (
            candidate_verification["score"],
            candidate_verification["pose_score"],
            candidate_verification["identity_score"],
        )
        append_pipeline_log(
            pipeline_id,
            f"Pose stabilizer pass {pass_number} audit: overall={new_quality[0]:.2f}, "
            f"identity={new_quality[2]:.2f}, pose={new_quality[1]:.2f}",
        )
        if new_quality <= old_quality:
            append_pipeline_log(
                pipeline_id,
                f"Pose stabilizer pass {pass_number} was not an improvement; preserving the prior views",
            )
            break

        for index, candidate in enumerate(candidate_files):
            shutil.copy2(candidate, view_files[index])
        candidate_verification["notes"].insert(
            0,
            f"Rigid-pose stabilization pass {pass_number} corrected {repaired_count} view(s).",
        )
        accepted_verification = candidate_verification
        accepted_passes += 1
        stabilized_views += repaired_count
        write_pipeline(
            pipeline_id,
            stabilization_passes=accepted_passes,
            stabilized_views=stabilized_views,
        )

    return accepted_verification


def generate_turntable_views(pipeline_id: str) -> list[Path]:
    metadata = read_pipeline(pipeline_id)
    directory = PIPELINE_ROOT / pipeline_id
    views_dir = directory / "views"
    views_dir.mkdir(exist_ok=True)
    references: list[tuple[bytes, str]] = []
    for reference in metadata["references"]:
        references.append(((directory / "references" / reference["filename"]).read_bytes(), reference["mime_type"]))

    client = genai.Client(
        vertexai=True,
        project=NANO_BANANA_PROJECT,
        location=NANO_BANANA_LOCATION,
        http_options=genai_types.HttpOptions(api_version="v1", timeout=300_000),
    )
    try:
        append_pipeline_log(pipeline_id, "Nano Banana Pro is composing the locked front anchor")
        response = client.models.generate_content(
            model=NANO_BANANA_ANCHOR_MODEL,
            contents=[
                *[
                    genai_types.Part.from_bytes(data=data, mime_type=mime_type)
                    for data, mime_type in references
                ],
                canonical_view_prompt(metadata["prompt"]),
            ],
            config=genai_types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
                image_config=genai_types.ImageConfig(aspect_ratio="1:1", image_size="2K"),
                temperature=0.1,
            ),
        )
        canonical_bytes, canonical_mime = response_image(response)
        canonical = directory / "canonical.png"
        save_normalized_view(canonical_bytes, canonical, None)

        append_pipeline_log(pipeline_id, "Nano Banana Pro is composing the locked rear anchor")
        canonical_payload = canonical.read_bytes()
        rear_response = client.models.generate_content(
            model=NANO_BANANA_ANCHOR_MODEL,
            contents=[
                genai_types.Part.from_bytes(data=canonical_payload, mime_type="image/png"),
                f"""Create exactly one rear three-quarter view of this exact frozen subject for metric 3D
reconstruction: {metadata['prompt']}
Rotate only the viewpoint exactly 180 degrees clockwise around the vertical axis. Preserve identical geometry,
proportions, pose, materials, colors, markings, seams, wear, accessories, and lighting. Fixed 50 mm lens,
{PIPELINE_ELEVATION:.0f} degree elevation, same distance, scale, framing, and uniform light-gray background.
This is an exact rear view: show only the back of the head and body. The nose axis stays parallel to the chest
normal, pointing away from the camera. No face, eye, nose, cheek, looking-back pose, head turn, or torso twist.
One complete subject, centered with 15 percent margin. No redesign, mirroring, deformation, text, border, grid,
collage, props, or extra objects.""",
            ],
            config=genai_types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
                image_config=genai_types.ImageConfig(aspect_ratio="1:1", image_size="2K"),
                temperature=0.0,
            ),
        )
        rear_bytes, _ = response_image(rear_response)
        rear = directory / "rear-anchor.png"
        with Image.open(canonical) as canonical_image:
            canonical_size = canonical_image.size
        save_normalized_view(rear_bytes, rear, canonical_size)

        def render_half_orbit(start: Path, end: Path, name: str, angle_range: str) -> Path:
            append_pipeline_log(pipeline_id, f"Veo is rendering the {angle_range} degree half-orbit")
            orbit_client = genai.Client(
                vertexai=True,
                project=NANO_BANANA_PROJECT,
                location=NANO_BANANA_LOCATION,
                http_options=genai_types.HttpOptions(api_version="v1", timeout=300_000),
            )
            try:
                operation = orbit_client.models.generate_videos(
                    model=VEO_MODEL,
                    prompt=f"""A strict calibrated photogrammetry capture of this exact frozen subject:
{metadata['prompt']}
The subject remains perfectly rigid and unchanged. The studio camera performs exactly one constant-speed
180-degree clockwise half-orbit ({angle_range} degrees) around the vertical axis and lands exactly on the supplied
last frame. Locked 50 mm lens, fixed {PIPELINE_ELEVATION:.0f} degree elevation, distance, scale, crop, horizon,
uniform light-gray background, and shadowless studio lighting. Smooth linear camera motion from the first frame;
no pause, easing, reversal, skipped arc, cut, zoom, animation, deformation, changing parts, text, or sound.""",
                    image=genai_types.Image(image_bytes=start.read_bytes(), mime_type="image/png"),
                    config=genai_types.GenerateVideosConfig(
                        number_of_videos=1,
                        output_gcs_uri=f"gs://{VEO_OUTPUT_BUCKET}/pipelines/{pipeline_id}/veo/{name}/",
                        duration_seconds=8,
                        aspect_ratio="16:9",
                        resolution="720p",
                        generate_audio=False,
                        enhance_prompt=True,
                        last_frame=genai_types.Image(image_bytes=end.read_bytes(), mime_type="image/png"),
                    ),
                )
                append_pipeline_log(pipeline_id, f"Veo {name} operation: {operation.name}")
                while not operation.done:
                    time.sleep(10)
                    operation = orbit_client.operations.get(operation)
                if operation.error:
                    raise RuntimeError(f"Veo {name} failed: {str(operation.error)[:400]}")
                video_uri = operation.result.generated_videos[0].video.uri
                if not video_uri or not video_uri.startswith("gs://"):
                    raise RuntimeError(f"Veo {name} completed without a Cloud Storage video")
                bucket_name, object_name = video_uri[5:].split("/", 1)
                video = directory / f"{name}.mp4"
                storage_client = storage.Client(project=NANO_BANANA_PROJECT)
                try:
                    storage_client.bucket(bucket_name).blob(object_name).download_to_filename(video)
                finally:
                    storage_client.close()
                append_pipeline_log(pipeline_id, f"Downloaded {name} from {video_uri}")
                return video
            finally:
                orbit_client.close()

        append_pipeline_log(pipeline_id, "Rendering both Veo half-orbits concurrently")
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="veo-orbit") as executor:
            first_future = executor.submit(
                render_half_orbit, canonical, rear, "orbit-000-180", "0 to 180"
            )
            second_future = executor.submit(
                render_half_orbit, rear, canonical, "orbit-180-360", "180 to 360"
            )
            first_video = first_future.result()
            second_video = second_future.result()
        first_frames = extract_half_orbit_frames(first_video, directory / "first-half")
        write_pipeline(pipeline_id, generated_views=PIPELINE_HALF_VIEW_COUNT, current_angle=150.0)
        second_frames = extract_half_orbit_frames(second_video, directory / "second-half")
        view_files = []
        for source in [*first_frames, *second_frames]:
            target = views_dir / f"{len(view_files):03d}.png"
            shutil.copy2(source, target)
            view_files.append(target)
        write_pipeline(pipeline_id, generated_views=PIPELINE_VIEW_COUNT, current_angle=None)
        write_pipeline(pipeline_id, status="verifying_views")
        append_pipeline_log(pipeline_id, "Running semantic identity and closed-orbit audit")
        verification = verify_turntable(client, view_files, metadata["angles"], metadata["prompt"])
        if (
            verification["score"] < PIPELINE_MIN_USABLE_COHERENCE
            and verification["pose_score"] < PIPELINE_COHERENCE_THRESHOLD
        ):
            append_pipeline_log(
                pipeline_id,
                "Pose drift detected; invoking the rigid-subject stabilization layer",
            )
            verification = stabilize_turntable_views(
                client,
                pipeline_id,
                view_files,
                metadata["angles"],
                canonical,
                rear,
                metadata["prompt"],
                verification,
            )
        rejected_indices = verifier_issue_indices(verification, len(view_files))
        if verification["score"] < PIPELINE_COHERENCE_THRESHOLD:
            if (
                metadata["method"] != "hunyuan3d"
                and rejected_indices
                and len(view_files) - len(rejected_indices) >= 10
            ):
                rejected = set(rejected_indices)
                repaired = directory / "repaired-views"
                repaired.mkdir(exist_ok=True)
                kept_angles: list[float] = []
                kept_elevations: list[float] = []
                for old_index, source in enumerate(view_files):
                    if old_index in rejected:
                        continue
                    new_index = len(kept_angles)
                    shutil.copy2(source, repaired / f"{new_index:03d}.png")
                    kept_angles.append(metadata["angles"][old_index])
                    kept_elevations.append(metadata["elevations"][old_index])
                shutil.rmtree(views_dir)
                repaired.replace(views_dir)
                view_files = [views_dir / f"{index:03d}.png" for index in range(len(kept_angles))]
                metadata["angles"] = kept_angles
                metadata["elevations"] = kept_elevations
                metadata["view_count"] = len(kept_angles)
                append_pipeline_log(
                    pipeline_id,
                    f"Removed verifier-rejected frames {sorted(rejected)}; re-auditing {len(view_files)} views",
                )
                write_pipeline(
                    pipeline_id,
                    angles=kept_angles,
                    elevations=kept_elevations,
                    view_count=len(kept_angles),
                    generated_views=len(kept_angles),
                )
                verification = verify_turntable(client, view_files, kept_angles, metadata["prompt"])
                verification["notes"].insert(
                    0,
                    f"Removed {len(rejected)} verifier-rejected video frame(s) before reconstruction.",
                )
        notes = verification["notes"]
        notes.extend(
            str(issue.get("reason", "View inconsistency detected"))[:300]
            for issue in verification["issues"][:5]
            if isinstance(issue, dict)
        )
        audit_changes: dict[str, object] = {
            "coherence_score": verification["score"],
            "verification_notes": notes[:8],
        }
        if verification["estimated_azimuths"] is not None:
            audit_changes["angles"] = verification["estimated_azimuths"]
            append_pipeline_log(pipeline_id, "Applied verifier-estimated azimuths to the camera transforms")
        write_pipeline(pipeline_id, **audit_changes)
        append_pipeline_log(
            pipeline_id,
            f"Consistency audit: overall={verification['score']:.2f}, "
            f"identity={verification['identity_score']:.2f}, pose={verification['pose_score']:.2f}",
        )
        if (
            verification["score"] < PIPELINE_COHERENCE_THRESHOLD
            and not reconstruction_views_usable(verification)
        ):
            raise RuntimeError(
                f"Generated views are unusable for reconstruction (coherence "
                f"{verification['score']:.2f}, identity {verification['identity_score']:.2f})"
            )
        if verification["score"] < PIPELINE_COHERENCE_THRESHOLD:
            record_soft_coherence_warning(pipeline_id, verification)
            write_pipeline(
                pipeline_id,
                verification_notes=verification["notes"][:8],
            )
        return view_files
    finally:
        client.close()


def vector_normalize(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(sum(value * value for value in vector))
    return tuple(value / length for value in vector)  # type: ignore[return-value]


def vector_cross(
    left: tuple[float, float, float], right: tuple[float, float, float]
) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def orbit_transform(angle_degrees: float, elevation_degrees: float = 15.0, radius: float = 2.0) -> list[list[float]]:
    azimuth = math.radians(angle_degrees)
    elevation = math.radians(elevation_degrees)
    position = (
        radius * math.cos(elevation) * math.cos(azimuth),
        radius * math.cos(elevation) * math.sin(azimuth),
        radius * math.sin(elevation),
    )
    forward = vector_normalize(tuple(-value for value in position))  # type: ignore[arg-type]
    right = vector_normalize(vector_cross(forward, (0.0, 0.0, 1.0)))
    up = vector_cross(right, forward)
    return [
        [right[0], up[0], -forward[0], position[0]],
        [right[1], up[1], -forward[1], position[1]],
        [right[2], up[2], -forward[2], position[2]],
        [0.0, 0.0, 0.0, 1.0],
    ]


def create_foreground_mask(source: Path, destination: Path) -> None:
    """Fit and subtract the smooth studio background, then retain the subject.

    A single corner color is not sufficient because generated seamless
    backgrounds contain a radial luminance gradient. Fitting a low-order 2-D
    surface to uncontaminated border pixels prevents that gradient from being
    classified as one giant foreground component.
    """
    with Image.open(source) as opened:
        image = opened.convert("RGB")
    width, height = image.size
    array = np.asarray(image, dtype=np.float32)
    y, x = np.mgrid[0:height, 0:width]
    xn = (x / max(1, width - 1)) * 2.0 - 1.0
    yn = (y / max(1, height - 1)) * 2.0 - 1.0
    border = (
        (y < height * 0.07)
        | (x < width * 0.055)
        | (x >= width * 0.945)
        | ((y >= height * 0.94) & ((x < width * 0.14) | (x >= width * 0.86)))
    )
    features = np.stack(
        (
            np.ones_like(xn),
            xn,
            yn,
            xn * xn,
            yn * yn,
            xn * yn,
            xn * xn * yn,
            xn * yn * yn,
        ),
        axis=-1,
    )
    coefficients, *_ = np.linalg.lstsq(features[border], array[border], rcond=None)
    predicted = np.clip(features @ coefficients, 0, 255)
    residual = np.max(np.abs(array - predicted), axis=2)
    threshold = max(14.0, float(np.percentile(residual[border], 99.5)) + 3.0)
    mask = Image.fromarray((residual >= threshold).astype(np.uint8) * 255)
    mask = mask.filter(ImageFilter.MaxFilter(11)).filter(ImageFilter.MinFilter(5))
    pixels = mask.tobytes()
    visited = bytearray(width * height)
    largest: list[int] = []
    for seed, value in enumerate(pixels):
        if value == 0 or visited[seed]:
            continue
        component: list[int] = []
        pending: deque[int] = deque([seed])
        visited[seed] = 1
        while pending:
            current = pending.popleft()
            component.append(current)
            x, y = current % width, current // width
            for neighbor in (
                current - 1 if x else -1,
                current + 1 if x + 1 < width else -1,
                current - width if y else -1,
                current + width if y + 1 < height else -1,
            ):
                if neighbor >= 0 and not visited[neighbor] and pixels[neighbor]:
                    visited[neighbor] = 1
                    pending.append(neighbor)
        if len(component) > len(largest):
            largest = component
    clean = bytearray(width * height)
    for index in largest:
        clean[index] = 255
    foreground_fraction = len(largest) / (width * height)
    if not 0.03 <= foreground_fraction <= 0.78:
        raise RuntimeError(
            f"Foreground segmentation is implausible ({foreground_fraction:.1%} of frame)"
        )
    Image.frombytes("L", (width, height), bytes(clean)).save(destination, "PNG", optimize=True)


def build_turntable_dataset(pipeline_id: str, view_files: list[Path]) -> Path:
    metadata = read_pipeline(pipeline_id)
    directory = PIPELINE_ROOT / pipeline_id
    dataset = directory / "dataset"
    images_dir = dataset / "images"
    masks_dir = dataset / "masks"
    images_dir.mkdir(parents=True, exist_ok=True)
    masks_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(view_files[0]) as first:
        width, height = first.size
    # Veo renders a 16:9 frame at roughly a 40-degree horizontal FOV. The
    # reconstruction uses its centered square crop, whose FOV is narrower.
    cropped_horizontal_fov = math.degrees(
        2.0 * math.atan((9.0 / 16.0) * math.tan(math.radians(40.0) / 2.0))
    )
    focal = 0.5 * width / math.tan(math.radians(cropped_horizontal_fov) / 2.0)
    frames = []
    elevations = metadata.get("elevations") or [PIPELINE_ELEVATION] * len(view_files)
    for index, (source, angle, elevation) in enumerate(zip(view_files, metadata["angles"], elevations)):
        filename = f"{index:03d}.png"
        shutil.copy2(source, images_dir / filename)
        create_foreground_mask(source, masks_dir / filename)
        frames.append(
            {
                "file_path": f"images/{filename}",
                "mask_path": f"masks/{filename}",
                "transform_matrix": orbit_transform(angle, elevation),
            }
        )
    transforms = {
        "camera_model": "OPENCV",
        "fl_x": focal,
        "fl_y": focal,
        "cx": width / 2.0,
        "cy": height / 2.0,
        "w": width,
        "h": height,
        "k1": 0.0,
        "k2": 0.0,
        "p1": 0.0,
        "p2": 0.0,
        "frames": frames,
    }
    (dataset / "transforms.json").write_text(json.dumps(transforms, indent=2))
    archive = Path(shutil.make_archive(str(directory / "dataset"), "zip", dataset))
    append_pipeline_log(pipeline_id, f"Built masked posed Nerfstudio dataset with {len(frames)} frames")
    return archive


async def run_nano_request(
    prompt: str,
    images: list[tuple[bytes, str]],
    model: str,
    aspect_ratio: str,
    image_size: str,
    response_format: str,
) -> Response:
    validate_nano_options(prompt, model, aspect_ratio, image_size, response_format)
    try:
        output_images, output_text = await asyncio.to_thread(
            nano_banana_generate,
            prompt.strip(),
            images,
            model,
            aspect_ratio,
            image_size,
        )
    except Exception as exc:
        logger.exception("Nano Banana request failed")
        raise HTTPException(502, f"Nano Banana request failed: {str(exc)[:300]}") from exc
    return nano_response(output_images, output_text, model, response_format)


async def read_nano_uploads(uploads: list[UploadFile]) -> list[tuple[bytes, str]]:
    if not uploads or len(uploads) > 14:
        raise HTTPException(422, "Provide between 1 and 14 reference images")
    total = 0
    payloads: list[tuple[bytes, str]] = []
    try:
        for upload in uploads:
            mime_type = (upload.content_type or "").lower()
            if mime_type not in NANO_INPUT_MIME_TYPES:
                raise HTTPException(422, f"Unsupported image MIME type: {mime_type or 'unknown'}")
            data = await upload.read(NANO_MAX_INPUT_BYTES + 1)
            total += len(data)
            if total > NANO_MAX_INPUT_BYTES:
                raise HTTPException(413, "Reference images exceed the 50 MB combined limit")
            if not data:
                raise HTTPException(422, "Reference images cannot be empty")
            payloads.append((data, mime_type))
    finally:
        for upload in uploads:
            await upload.close()
    return payloads


def prepare_data(directory: Path) -> bool:
    upload = directory / "dataset.zip"
    extracted = directory / "input"
    extracted.mkdir()
    safe_extract(upload, extracted)
    upload.unlink(missing_ok=True)

    transforms = list(extracted.rglob("transforms.json"))
    if transforms:
        source = transforms[0].parent
        shutil.copytree(source, directory / "processed", dirs_exist_ok=True)
        return False

    images = [p for p in extracted.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES]
    if len(images) < 10:
        raise ValueError("At least 10 supported images are required")
    raw = directory / "raw_images"
    raw.mkdir()
    for index, image in enumerate(sorted(images)):
        shutil.copy2(image, raw / f"{index:06d}{image.suffix.lower()}")
    return True


def execute_job(job_id: str) -> None:
    directory = DATA_ROOT / job_id
    try:
        write_job(job_id, status="processing")
        needs_processing = prepare_data(directory)
        if needs_processing:
            run_command(
                job_id,
                [
                    "ns-process-data",
                    "images",
                    "--data",
                    "/workspace/raw_images",
                    "--output-dir",
                    "/workspace/processed",
                ],
            )

        metadata = read_job(job_id)
        write_job(job_id, status="training")
        training_args = [
            "ns-train",
            metadata["method"],
            "--data",
            "/workspace/processed",
            "--output-dir",
            "/workspace/outputs",
            "--max-num-iterations",
            str(metadata["iterations"]),
            "--steps-per-eval-batch",
            str(metadata["iterations"] + 1),
            "--steps-per-eval-image",
            str(metadata["iterations"] + 1),
            "--steps-per-eval-all-images",
            str(metadata["iterations"] + 1),
        ]
        if metadata["method"].startswith("splatfacto"):
            training_args.extend(
                [
                    "--pipeline.model.background-color",
                    "random",
                    "--pipeline.model.cull-alpha-thresh",
                    "0.05",
                    "--pipeline.model.random-scale",
                    "0.65",
                    "--pipeline.model.use-scale-regularization",
                    "True",
                    "--pipeline.model.camera-optimizer.mode",
                    "SO3xR3",
                ]
            )
        else:
            training_args.extend(
                [
                    "--pipeline.model.disable-scene-contraction",
                    "True",
                    "--pipeline.model.background-color",
                    "random",
                    "--pipeline.model.collider-params",
                    "near_plane",
                    "0.05",
                    "far_plane",
                    "3.0",
                    "--pipeline.model.near-plane",
                    "0.05",
                    "--pipeline.model.far-plane",
                    "3.0",
                ]
            )
        training_args.extend(["--vis", "tensorboard"])
        run_command(job_id, training_args)

        configs = sorted((directory / "outputs").rglob("config.yml"), key=lambda p: p.stat().st_mtime)
        if not configs:
            raise RuntimeError("Training completed without a config.yml")
        config = "/workspace/" + str(configs[-1].relative_to(directory)).replace("\\", "/")

        write_job(job_id, status="exporting")
        if metadata["output_type"] == "pointcloud":
            if metadata["method"].startswith("splatfacto"):
                run_command(
                    job_id,
                    [
                        "ns-export",
                        "gaussian-splat",
                        "--load-config",
                        config,
                        "--output-dir",
                        "/workspace/export/splat",
                        "--output-filename",
                        "splat.ply",
                        "--obb-center",
                        "0",
                        "0",
                        "-0.05",
                        "--obb-scale",
                        "0.9",
                        "0.9",
                        "0.7",
                        "--ply-color-mode",
                        "rgb",
                    ],
                )
                convert_rgb_splat_to_sh(directory / "export" / "splat" / "splat.ply")
            else:
                run_command(
                    job_id,
                    [
                        "ns-export",
                        "pointcloud",
                        "--load-config",
                        config,
                        "--output-dir",
                        "/workspace/export/pointcloud",
                        "--num-points",
                        "1000000",
                        "--normal-method",
                        "open3d",
                        "--obb-center",
                        "0",
                        "0",
                        "0",
                        "--obb-scale",
                        "2.4",
                        "2.4",
                        "2.4",
                        "--std-ratio",
                        "3.0",
                    ],
                )
                run_command(
                    job_id,
                    [
                        "ns-export",
                        "poisson",
                        "--load-config",
                        config,
                        "--output-dir",
                        "/workspace/export/mesh",
                        "--num-points",
                        "750000",
                        "--normal-method",
                        "open3d",
                        "--obb-center",
                        "0",
                        "0",
                        "0",
                        "--obb-scale",
                        "2.4",
                        "2.4",
                        "2.4",
                        "--std-ratio",
                        "3.0",
                        "--texture-method",
                        "nerf",
                        "--num-pixels-per-side",
                        "2048",
                        "--target-num-faces",
                        "60000",
                    ],
                )

        archive_base = directory / "result"
        include = directory / ("export" if metadata["output_type"] == "pointcloud" else "outputs")
        shutil.make_archive(str(archive_base), "zip", include)
        write_job(job_id, status="complete", result_ready=True)
    except Exception as exc:  # The sanitized error is returned; details remain in job.log.
        write_job(job_id, status="failed", error=str(exc)[:500])


def create_pipeline_child(pipeline_id: str, archive: Path) -> str:
    metadata = read_pipeline(pipeline_id)
    child_id = str(uuid.uuid4())
    child_dir = DATA_ROOT / child_id
    child_dir.mkdir(parents=True)
    shutil.copy2(archive, child_dir / "dataset.zip")
    timestamp = now()
    child = {
        "id": child_id,
        "status": "queued",
        "method": metadata["method"],
        "iterations": metadata["iterations"],
        "output_type": "pointcloud",
        "created_at": timestamp,
        "updated_at": timestamp,
        "error": None,
        "result_ready": False,
    }
    (child_dir / "job.json").write_text(json.dumps(child, indent=2))
    write_pipeline(
        pipeline_id,
        status="training",
        error=None,
        result_ready=False,
        nerfstudio_job_id=child_id,
        nerfstudio_status="queued",
    )
    append_pipeline_log(pipeline_id, f"Queued Nerfstudio job {child_id}")
    return child_id


def execute_pipeline(pipeline_id: str) -> str | None:
    try:
        write_pipeline(pipeline_id, status="generating_views")
        view_files = generate_turntable_views(pipeline_id)
        write_pipeline(pipeline_id, status="preparing_dataset")
        archive = build_turntable_dataset(pipeline_id, view_files)
        if read_pipeline(pipeline_id)["method"] == "hunyuan3d":
            execute_hunyuan_pipeline(pipeline_id)
            return None
        return create_pipeline_child(pipeline_id, archive)
    except Exception as exc:
        logger.exception("Turntable pipeline failed")
        write_pipeline(pipeline_id, status="failed", error=str(exc)[:500])
        append_pipeline_log(pipeline_id, f"Pipeline failed: {str(exc)[:500]}")
        return None


def execute_hunyuan_pipeline(pipeline_id: str) -> None:
    """Build a coherent textured mesh from synthetic cardinal views."""
    directory = pipeline_dir(pipeline_id)
    metadata = read_pipeline(pipeline_id)
    quality_profiles = {
        5000: {"steps": 24, "octree_resolution": 420, "target_faces": 140_000, "render_size": 1024, "texture_size": 1536},
        10000: {"steps": 40, "octree_resolution": 480, "target_faces": 300_000, "render_size": 1536, "texture_size": 2048},
        30000: {"steps": 55, "octree_resolution": 512, "target_faces": 400_000, "render_size": 2048, "texture_size": 2048},
    }
    profile = quality_profiles.get(
        int(metadata.get("iterations", 10000)), quality_profiles[10000]
    )
    output = directory / "hunyuan"
    output.mkdir(exist_ok=True)
    write_pipeline(
        pipeline_id,
        status="training",
        nerfstudio_status="geometry_and_texture",
        result_ready=False,
        error=None,
    )
    append_pipeline_log(
        pipeline_id,
        "Starting GPU-priority Hunyuan3D solve: "
        f"{profile['steps']} geometry steps at octree {profile['octree_resolution']}, "
        f"{profile['target_faces']:,} texture faces, "
        f"{profile['texture_size']}px texture",
    )
    environment = os.environ.copy()
    environment.update(
        HOME="/var/lib/nerfstudio-api",
        HF_HOME="/var/lib/nerfstudio-api/hf",
        PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True",
        OMP_NUM_THREADS="12",
        MKL_NUM_THREADS="12",
        OPENBLAS_NUM_THREADS="12",
        NUMEXPR_NUM_THREADS="12",
    )
    log_path = directory / "pipeline.log"
    generate = [
        str(HUNYUAN_PYTHON),
        str(HUNYUAN_ROOT / "youcam_generate.py"),
        "--dataset",
        str(directory / "dataset"),
        "--output",
        str(output),
        "--steps",
        str(profile["steps"]),
        "--octree-resolution",
        str(profile["octree_resolution"]),
        "--target-faces",
        str(profile["target_faces"]),
        "--component-proximity",
        "0.06",
        "--render-size",
        str(profile["render_size"]),
        "--texture-size",
        str(profile["texture_size"]),
        "--skip-delight",
    ]
    clean = [
        str(HUNYUAN_PYTHON),
        str(HUNYUAN_ROOT / "youcam_clean_mesh.py"),
        str(output / "model.glb"),
        str(output / "model-final.glb"),
        "--min-faces",
        "3000",
        "--proximity",
        "0.06",
    ]
    with log_path.open("a", encoding="utf-8") as log:
        subprocess.run(generate, cwd=HUNYUAN_ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=2700)
        subprocess.run(clean, cwd=HUNYUAN_ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
    result = directory / "result.zip"
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.write(output / "model-final.glb", "model/model.glb")
        report = output / "report.json"
        if report.is_file():
            archive.write(report, "model/report.json")
    write_pipeline(
        pipeline_id,
        status="complete",
        nerfstudio_status="complete",
        result_ready=True,
        verification_notes=list(metadata.get("verification_notes", []))[:6] + [
            "Semantic orbit passed; geometry-first multiview reconstruction used for synthetic inputs.",
            "GPU-priority mesh reduction removed UV-unwrapping bottlenecks before texture generation.",
            "Detached background, mask-floor, and floating components were removed before export.",
        ],
    )
    append_pipeline_log(pipeline_id, "Hunyuan3D textured GLB exported and cleaned")


def recheck_pipeline(pipeline_id: str) -> str | None:
    """Re-audit stored frames, remove verifier-identified outliers, and queue reconstruction."""
    try:
        metadata = read_pipeline(pipeline_id)
        views_dir = pipeline_dir(pipeline_id) / "views"
        view_files = [views_dir / f"{index:03d}.png" for index in range(metadata["generated_views"])]
        if len(view_files) < 10 or not all(path.is_file() for path in view_files):
            raise RuntimeError("At least ten stored frames are required for recheck")
        write_pipeline(pipeline_id, status="verifying_views", error=None)
        client = genai.Client(
            vertexai=True,
            project=NANO_BANANA_PROJECT,
            location=NANO_BANANA_LOCATION,
            http_options=genai_types.HttpOptions(api_version="v1", timeout=300_000),
        )
        try:
            verification = verify_turntable(client, view_files, metadata["angles"], metadata["prompt"])
            canonical = pipeline_dir(pipeline_id) / "canonical.png"
            rear = pipeline_dir(pipeline_id) / "rear-anchor.png"
            if (
                verification["score"] < PIPELINE_MIN_USABLE_COHERENCE
                and verification["pose_score"] < PIPELINE_COHERENCE_THRESHOLD
                and int(metadata.get("stabilized_views", 0)) < len(view_files)
                and canonical.is_file()
                and rear.is_file()
            ):
                append_pipeline_log(
                    pipeline_id,
                    "Recheck detected pose drift; invoking the rigid-subject stabilization layer",
                )
                verification = stabilize_turntable_views(
                    client,
                    pipeline_id,
                    view_files,
                    metadata["angles"],
                    canonical,
                    rear,
                    metadata["prompt"],
                    verification,
                )
            rejected = (
                set(verifier_issue_indices(verification, len(view_files)))
                if verification["score"] < PIPELINE_COHERENCE_THRESHOLD
                else set()
            )
            if (
                metadata["method"] != "hunyuan3d"
                and rejected
                and len(view_files) - len(rejected) >= 10
            ):
                repaired = pipeline_dir(pipeline_id) / "rechecked-views"
                repaired.mkdir(exist_ok=True)
                angles: list[float] = []
                elevations: list[float] = []
                for old_index, source in enumerate(view_files):
                    if old_index in rejected:
                        continue
                    shutil.copy2(source, repaired / f"{len(angles):03d}.png")
                    angles.append(metadata["angles"][old_index])
                    elevations.append(metadata["elevations"][old_index])
                shutil.rmtree(views_dir)
                repaired.replace(views_dir)
                view_files = [views_dir / f"{index:03d}.png" for index in range(len(angles))]
                metadata["angles"], metadata["elevations"] = angles, elevations
                metadata["view_count"] = metadata["generated_views"] = len(angles)
                write_pipeline(
                    pipeline_id,
                    angles=angles,
                    elevations=elevations,
                    view_count=len(angles),
                    generated_views=len(angles),
                )
                append_pipeline_log(pipeline_id, f"Recheck removed verifier-rejected frames {sorted(rejected)}")
                verification = verify_turntable(client, view_files, angles, metadata["prompt"])
                verification["notes"].insert(0, f"Removed {len(rejected)} rejected frame(s) during recheck.")
        finally:
            client.close()
        recheck_notes = list(verification["notes"])
        recheck_notes.extend(
            str(issue.get("reason", "View inconsistency detected"))[:300]
            for issue in verification["issues"][:5]
            if isinstance(issue, dict)
        )
        write_pipeline(
            pipeline_id,
            coherence_score=verification["score"],
            verification_notes=recheck_notes[:8],
        )
        append_pipeline_log(pipeline_id, f"Recheck audit score: {verification['score']:.2f}")
        if (
            verification["score"] < PIPELINE_COHERENCE_THRESHOLD
            and not reconstruction_views_usable(verification)
        ):
            raise RuntimeError(
                f"Stored views are unusable for reconstruction (coherence "
                f"{verification['score']:.2f}, identity {verification['identity_score']:.2f})"
            )
        if verification["score"] < PIPELINE_COHERENCE_THRESHOLD:
            record_soft_coherence_warning(pipeline_id, verification)
            write_pipeline(
                pipeline_id,
                verification_notes=verification["notes"][:8],
            )
        write_pipeline(pipeline_id, status="preparing_dataset")
        archive = build_turntable_dataset(pipeline_id, view_files)
        if read_pipeline(pipeline_id)["method"] == "hunyuan3d":
            execute_hunyuan_pipeline(pipeline_id)
            return None
        return create_pipeline_child(pipeline_id, archive)
    except Exception as exc:
        logger.exception("Pipeline recheck failed")
        write_pipeline(pipeline_id, status="failed", error=str(exc)[:500])
        append_pipeline_log(pipeline_id, f"Recheck failed: {str(exc)[:500]}")
        return None


async def worker() -> None:
    while True:
        job_id = await queue.get()
        try:
            await asyncio.to_thread(execute_job, job_id)
        finally:
            queue.task_done()


async def pipeline_worker() -> None:
    while True:
        pipeline_id = await pipeline_queue.get()
        try:
            child_id = await asyncio.to_thread(execute_pipeline, pipeline_id)
            if child_id:
                await queue.put(child_id)
        finally:
            pipeline_queue.task_done()


async def style_worker() -> None:
    while True:
        style_id = await style_queue.get()
        try:
            await asyncio.to_thread(execute_style_job, style_id)
        finally:
            style_queue.task_done()


@app.on_event("startup")
async def startup() -> None:
    global worker_task, pipeline_worker_task, style_worker_task
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    PIPELINE_ROOT.mkdir(parents=True, exist_ok=True)
    STYLE_ROOT.mkdir(parents=True, exist_ok=True)
    for metadata in DATA_ROOT.glob("*/job.json"):
        try:
            data = json.loads(metadata.read_text())
            if data.get("status") in {"queued", "processing", "training", "exporting"}:
                write_job(data["id"], status="failed", error="API restarted during job")
        except Exception:
            continue
    for metadata in PIPELINE_ROOT.glob("*/pipeline.json"):
        try:
            data = json.loads(metadata.read_text())
            interrupted_direct_training = data.get("status") == "training" and not data.get("nerfstudio_job_id")
            if data.get("status") in {
                "queued",
                "generating_views",
                "stabilizing_views",
                "verifying_views",
                "preparing_dataset",
            } or interrupted_direct_training:
                write_pipeline(data["id"], status="failed", error="API restarted during pipeline execution")
        except Exception:
            continue
    for metadata in STYLE_ROOT.glob("*/style.json"):
        try:
            data = json.loads(metadata.read_text())
            if data.get("status") in {
                "queued",
                "downloading_media",
                "selecting_garment",
                "uploading_assets",
                "generating_tryon",
            }:
                write_style(data["id"], status="failed", error="API restarted during style generation")
        except Exception:
            continue
    worker_task = asyncio.create_task(worker())
    pipeline_worker_task = asyncio.create_task(pipeline_worker())
    style_worker_task = asyncio.create_task(style_worker())


@app.on_event("shutdown")
async def shutdown() -> None:
    if worker_task:
        worker_task.cancel()
    if pipeline_worker_task:
        pipeline_worker_task.cancel()
    if style_worker_task:
        style_worker_task.cancel()


@app.get("/health")
def health() -> dict:
    gpu = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    image = subprocess.run(
        ["docker", "image", "inspect", NERFSTUDIO_IMAGE],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return {
        "status": "ok" if gpu.returncode == 0 and image.returncode == 0 else "degraded",
        "gpu": gpu.stdout.strip() if gpu.returncode == 0 else None,
        "nerfstudio_image_ready": image.returncode == 0,
        "nano_banana_model": NANO_BANANA_MODEL,
        "queue_depth": queue.qsize(),
        "style_queue_depth": style_queue.qsize(),
        "youcam_clothes_v3_configured": bool(load_youcam_api_key()),
    }


@app.get("/v1", dependencies=[Depends(authenticate)])
def api_info() -> dict:
    return {
        "service": "Nerfstudio A100 API",
        "workflow": "POST /v1/jobs, GET /v1/jobs/{id}, GET /v1/jobs/{id}/result",
        "methods": sorted(ALLOWED_METHODS),
        "outputs": ["pointcloud", "model"],
        "nano_banana": {
            "generate": "POST /v1/images/generate",
            "edit": "POST /v1/images/edit",
            "default_model": NANO_BANANA_MODEL,
            "models": sorted(NANO_BANANA_MODELS),
        },
        "reconstruction_pipeline": {
            "create": "POST /v1/pipelines",
            "status": "GET /v1/pipelines/{id}",
            "model": "GET /v1/pipelines/{id}/model",
        },
        "fashion_pipeline": {
            "create_preview": "POST /v1/style-jobs",
            "status": "GET /v1/style-jobs/{id}",
            "approve_for_3d": "POST /v1/style-jobs/{id}/approve",
            "providers": "GET /v1/clothing-providers",
        },
    }


@app.post("/v1/images/generate", dependencies=[Depends(authenticate)])
async def generate_image(
    prompt: Annotated[str, Form()],
    model: Annotated[str, Form()] = NANO_BANANA_MODEL,
    aspect_ratio: Annotated[str, Form()] = "1:1",
    image_size: Annotated[str, Form()] = "1K",
    response_format: Annotated[str, Form()] = "image",
) -> Response:
    """Generate an image with Nano Banana from a text prompt."""
    return await run_nano_request(prompt, [], model, aspect_ratio, image_size, response_format)


@app.post("/v1/images/edit", dependencies=[Depends(authenticate)])
async def edit_image(
    prompt: Annotated[str, Form()],
    images: Annotated[list[UploadFile], File(description="One or more reference images")],
    model: Annotated[str, Form()] = NANO_BANANA_MODEL,
    aspect_ratio: Annotated[str, Form()] = "1:1",
    image_size: Annotated[str, Form()] = "1K",
    response_format: Annotated[str, Form()] = "image",
) -> Response:
    """Edit or combine reference images with Nano Banana."""
    payloads = await read_nano_uploads(images)
    return await run_nano_request(prompt, payloads, model, aspect_ratio, image_size, response_format)


@app.get("/v1/style-jobs", response_model=list[StyleJob], dependencies=[Depends(authenticate)])
def list_style_jobs() -> list[dict]:
    jobs: list[dict] = []
    for path in sorted(
        STYLE_ROOT.glob("*/style.json"), key=lambda item: item.stat().st_mtime, reverse=True
    ):
        try:
            jobs.append(public_style(path.parent.name))
        except Exception:
            continue
        if len(jobs) >= 20:
            break
    return jobs


@app.get("/v1/clothing-providers", dependencies=[Depends(authenticate)])
def list_clothing_providers() -> list[dict]:
    return public_clothing_providers()


@app.post(
    "/v1/style-jobs",
    response_model=StyleJob,
    status_code=202,
    dependencies=[Depends(authenticate)],
)
async def create_style_job(
    identity_image: Annotated[UploadFile, File(description="Forward-facing identity photo")],
    instagram_url: Annotated[str, Form()] = "",
    garment_image: Annotated[UploadFile | None, File(description="Optional garment fallback")] = None,
    garment_category: Annotated[str, Form()] = "auto",
    provider: Annotated[str, Form()] = "auto",
    gender_mode: Annotated[str, Form()] = "female",
) -> dict:
    if garment_category not in {"auto", "upper_body", "lower_body", "full_body"}:
        raise HTTPException(422, "Invalid garment category")
    if gender_mode not in {"female", "male"}:
        raise HTTPException(422, "Gender mode must be female or male")
    try:
        resolve_clothing_provider(provider)
    except RuntimeError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not instagram_url.strip() and garment_image is None:
        raise HTTPException(422, "Provide an Instagram reel/post URL or a garment image")
    if instagram_url.strip():
        try:
            instagram_url = validate_instagram_url(instagram_url)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    allowed = {"image/jpeg", "image/png", "image/webp"}
    if (identity_image.content_type or "").lower() not in allowed:
        raise HTTPException(422, "Identity photo must be JPG, PNG, or WEBP")
    if garment_image and (garment_image.content_type or "").lower() not in allowed:
        raise HTTPException(422, "Garment fallback must be JPG, PNG, or WEBP")

    style_id = str(uuid.uuid4())
    directory = STYLE_ROOT / style_id
    directory.mkdir(parents=True)
    try:
        identity = await identity_image.read(10 * 1024**2 + 1)
        if len(identity) > 10 * 1024**2:
            raise HTTPException(413, "Identity photo exceeds the 10 MB limit")
        normalize_image_to_jpeg(identity, directory / "identity.jpg")
        has_fallback = garment_image is not None
        if garment_image:
            garment = await garment_image.read(10 * 1024**2 + 1)
            if len(garment) > 10 * 1024**2:
                raise HTTPException(413, "Garment image exceeds the 10 MB limit")
            normalize_image_to_jpeg(garment, directory / "garment-upload.jpg")
        timestamp = now()
        metadata = {
            "id": style_id,
            "status": "queued",
            "created_at": timestamp,
            "updated_at": timestamp,
            "instagram_url": instagram_url.strip(),
            "garment_category": garment_category,
            "garment_description": "",
            "source_type": "pending",
            "selected_frame": None,
            "has_fallback": has_fallback,
            "result_ready": False,
            "pipeline_id": None,
            "error": None,
            "provider_requested": provider,
            "provider_used": "",
            "gender_mode": gender_mode,
        }
        (directory / "style.json").write_text(json.dumps(metadata, indent=2))
        append_style_log(style_id, "Fashion transfer queued")
        await style_queue.put(style_id)
        return metadata
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    finally:
        await identity_image.close()
        if garment_image:
            await garment_image.close()


@app.get("/v1/style-jobs/{style_id}", response_model=StyleJob, dependencies=[Depends(authenticate)])
def get_style_job(style_id: str) -> dict:
    return public_style(style_id)


@app.get("/v1/style-jobs/{style_id}/garment", dependencies=[Depends(authenticate)])
def get_style_garment(style_id: str) -> FileResponse:
    metadata = read_style(style_id)
    if not metadata.get("selected_frame"):
        raise HTTPException(409, "Garment frame is not ready")
    path = style_dir(style_id) / "garment.jpg"
    if not path.is_file():
        raise HTTPException(404, "Garment frame not found")
    return FileResponse(path, media_type="image/jpeg", filename="garment-reference.jpg")


@app.get("/v1/style-jobs/{style_id}/result", dependencies=[Depends(authenticate)])
def get_style_result(style_id: str) -> FileResponse:
    metadata = read_style(style_id)
    path = style_dir(style_id) / "result.jpg"
    if not metadata.get("result_ready") or not path.is_file():
        raise HTTPException(409, "Virtual try-on result is not ready")
    return FileResponse(path, media_type="image/jpeg", filename="virtual-tryon.jpg")


@app.delete("/v1/style-jobs/{style_id}", dependencies=[Depends(authenticate)])
def delete_style_job(style_id: str) -> dict:
    metadata = read_style(style_id)
    if metadata["status"] in {
        "queued",
        "downloading_media",
        "selecting_garment",
        "uploading_assets",
        "generating_tryon",
    }:
        raise HTTPException(409, "A running style job cannot be deleted")
    shutil.rmtree(style_dir(style_id))
    return {"deleted": style_id}


@app.post(
    "/v1/style-jobs/{style_id}/approve",
    response_model=PipelineJob,
    status_code=202,
    dependencies=[Depends(authenticate)],
)
async def approve_style_for_reconstruction(
    style_id: str,
    prompt: Annotated[str, Form()] = "",
    method: Annotated[str, Form()] = "splatfacto",
    iterations: Annotated[int, Form(ge=PIPELINE_MIN_ITERATIONS, le=100000)] = 10000,
) -> dict:
    style = read_style(style_id)
    if style["status"] == "approved" and style.get("pipeline_id"):
        return synced_pipeline(style["pipeline_id"])
    if style["status"] != "complete" or not style.get("result_ready"):
        raise HTTPException(409, "Approve only after the virtual try-on preview is complete")
    if method not in {"hunyuan3d", "splatfacto", "nerfacto", "nerfacto-big"}:
        raise HTTPException(422, "Unsupported reconstruction method")
    if len(prompt) > 20_000:
        raise HTTPException(422, "Optional guidance cannot exceed 20,000 characters")

    styled_path = style_dir(style_id) / "result.jpg"
    identity_path = style_dir(style_id) / "identity.jpg"
    references = [
        (styled_path.read_bytes(), "image/jpeg"),
        (identity_path.read_bytes(), "image/jpeg"),
    ]
    guidance = (
        "The first reference is authoritative for the complete outfit, body, and pose. "
        "The second reference is authoritative only for facial identity. Preserve the generated outfit exactly. "
        f"Garment description: {style.get('garment_description', '')}. "
        + prompt.strip()
    )
    try:
        directive = await asyncio.to_thread(generate_subject_directive, references, guidance)
    except Exception as exc:
        logger.exception("Styled subject directive generation failed")
        raise HTTPException(502, "Could not prepare the styled subject for reconstruction") from exc

    pipeline_id = str(uuid.uuid4())
    directory = PIPELINE_ROOT / pipeline_id
    references_dir = directory / "references"
    references_dir.mkdir(parents=True)
    try:
        shutil.copy2(styled_path, references_dir / "reference-00.jpg")
        shutil.copy2(identity_path, references_dir / "reference-01.jpg")
        timestamp = now()
        angles = [round(index * 360.0 / PIPELINE_VIEW_COUNT, 4) for index in range(PIPELINE_VIEW_COUNT)]
        metadata = {
            "id": pipeline_id,
            "status": "queued",
            "prompt": directive,
            "user_guidance": prompt.strip(),
            "directive_generated": True,
            "view_count": PIPELINE_VIEW_COUNT,
            "generated_views": 0,
            "current_angle": None,
            "angles": angles,
            "elevations": [PIPELINE_ELEVATION] * PIPELINE_VIEW_COUNT,
            "method": method,
            "iterations": iterations,
            "created_at": timestamp,
            "updated_at": timestamp,
            "error": None,
            "nerfstudio_job_id": None,
            "nerfstudio_status": None,
            "result_ready": False,
            "view_urls": [],
            "coherence_score": None,
            "verification_notes": [],
            "stabilization_passes": 0,
            "stabilized_views": 0,
            "style_id": style_id,
            "references": [
                {"filename": "reference-00.jpg", "mime_type": "image/jpeg"},
                {"filename": "reference-01.jpg", "mime_type": "image/jpeg"},
            ],
        }
        (directory / "pipeline.json").write_text(json.dumps(metadata, indent=2))
        append_pipeline_log(pipeline_id, "Approved YouCam outfit preview as the canonical subject")
        write_style(style_id, status="approved", pipeline_id=pipeline_id)
        await pipeline_queue.put(pipeline_id)
        return metadata
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


@app.get("/v1/pipelines", response_model=list[PipelineJob], dependencies=[Depends(authenticate)])
def list_pipelines() -> list[dict]:
    pipelines: list[dict] = []
    for path in sorted(PIPELINE_ROOT.glob("*/pipeline.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            pipelines.append(synced_pipeline(path.parent.name))
        except Exception:
            continue
        if len(pipelines) >= 20:
            break
    return pipelines


@app.post("/v1/pipelines", response_model=PipelineJob, status_code=202, dependencies=[Depends(authenticate)])
async def create_pipeline(
    references: Annotated[list[UploadFile], File(description="Reference images defining the subject")],
    prompt: Annotated[str, Form()] = "",
    view_count: Annotated[int, Form(ge=12, le=12)] = PIPELINE_VIEW_COUNT,
    method: Annotated[str, Form()] = "splatfacto",
    iterations: Annotated[int, Form(ge=PIPELINE_MIN_ITERATIONS, le=100000)] = 10000,
) -> dict:
    if len(prompt) > 20_000:
        raise HTTPException(422, "Optional guidance cannot exceed 20,000 characters")
    if not references or len(references) > 8:
        raise HTTPException(422, "Provide between 1 and 8 reference images")
    if method not in {"hunyuan3d", "splatfacto", "nerfacto", "nerfacto-big"}:
        raise HTTPException(422, "method must be hunyuan3d, splatfacto, nerfacto, or nerfacto-big")

    pipeline_id = str(uuid.uuid4())
    directory = PIPELINE_ROOT / pipeline_id
    references_dir = directory / "references"
    references_dir.mkdir(parents=True)
    saved_references: list[dict[str, str]] = []
    reference_payloads: list[tuple[bytes, str]] = []
    total = 0
    try:
        for index, upload in enumerate(references):
            mime_type = (upload.content_type or "").lower()
            if mime_type not in NANO_INPUT_MIME_TYPES:
                raise HTTPException(422, f"Unsupported image MIME type: {mime_type or 'unknown'}")
            data = await upload.read(NANO_MAX_INPUT_BYTES + 1)
            total += len(data)
            if total > NANO_MAX_INPUT_BYTES:
                raise HTTPException(413, "Reference images exceed the 50 MB combined limit")
            extension = {
                "image/jpeg": "jpg",
                "image/webp": "webp",
                "image/heic": "heic",
                "image/heif": "heif",
            }.get(mime_type, "png")
            filename = f"reference-{index:02d}.{extension}"
            (references_dir / filename).write_bytes(data)
            saved_references.append({"filename": filename, "mime_type": mime_type})
            reference_payloads.append((data, mime_type))
        try:
            directive = await asyncio.to_thread(
                generate_subject_directive,
                reference_payloads,
                prompt,
            )
        except Exception as exc:
            logger.exception("Automatic subject directive generation failed")
            raise HTTPException(502, "Could not analyze the reference images; please retry") from exc
        timestamp = now()
        angles = [round(index * 360.0 / PIPELINE_VIEW_COUNT, 4) for index in range(PIPELINE_VIEW_COUNT)]
        elevations = [PIPELINE_ELEVATION] * PIPELINE_VIEW_COUNT
        metadata = {
            "id": pipeline_id,
            "status": "queued",
            "prompt": directive,
            "user_guidance": prompt.strip(),
            "directive_generated": True,
            "view_count": view_count,
            "generated_views": 0,
            "current_angle": None,
            "angles": angles,
            "elevations": elevations,
            "method": method,
            "iterations": iterations,
            "created_at": timestamp,
            "updated_at": timestamp,
            "error": None,
            "nerfstudio_job_id": None,
            "nerfstudio_status": None,
            "result_ready": False,
            "view_urls": [],
            "coherence_score": None,
            "verification_notes": [],
            "stabilization_passes": 0,
            "stabilized_views": 0,
            "references": saved_references,
        }
        (directory / "pipeline.json").write_text(json.dumps(metadata, indent=2))
        append_pipeline_log(pipeline_id, "Generated immutable subject directive from the uploaded references")
        append_pipeline_log(pipeline_id, f"Pipeline queued with {len(saved_references)} references and {view_count} views")
        await pipeline_queue.put(pipeline_id)
        return metadata
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    finally:
        for upload in references:
            await upload.close()


@app.get("/v1/pipelines/{pipeline_id}", response_model=PipelineJob, dependencies=[Depends(authenticate)])
def get_pipeline(pipeline_id: str) -> dict:
    return synced_pipeline(pipeline_id)


@app.post(
    "/v1/pipelines/{pipeline_id}/recheck",
    response_model=PipelineJob,
    dependencies=[Depends(authenticate)],
)
async def recheck_failed_pipeline(pipeline_id: str) -> dict:
    metadata = synced_pipeline(pipeline_id)
    if metadata["status"] != "failed" or metadata.get("nerfstudio_job_id"):
        raise HTTPException(409, "Only a failed pre-training pipeline can be rechecked")
    child_id = await asyncio.to_thread(recheck_pipeline, pipeline_id)
    if child_id:
        await queue.put(child_id)
    return synced_pipeline(pipeline_id)


@app.post(
    "/v1/pipelines/{pipeline_id}/retrain",
    response_model=PipelineJob,
    dependencies=[Depends(authenticate)],
)
async def retrain_failed_pipeline(
    pipeline_id: str,
    method: Annotated[str | None, Form()] = None,
) -> dict:
    metadata = synced_pipeline(pipeline_id)
    if metadata["status"] not in {"failed", "complete"}:
        raise HTTPException(409, "Only a finished pipeline with a prepared dataset can be retrained")
    views_dir = pipeline_dir(pipeline_id) / "views"
    view_files = [views_dir / f"{index:03d}.png" for index in range(metadata["generated_views"])]
    if len(view_files) < 10 or not all(path.is_file() for path in view_files):
        raise HTTPException(409, "Stored reconstruction views are incomplete")
    if method is not None:
        if method not in {"splatfacto", "nerfacto", "nerfacto-big"}:
            raise HTTPException(422, "method must be splatfacto, nerfacto, or nerfacto-big")
        write_pipeline(pipeline_id, method=method)
    archive = await asyncio.to_thread(build_turntable_dataset, pipeline_id, view_files)
    child_id = create_pipeline_child(pipeline_id, archive)
    await queue.put(child_id)
    append_pipeline_log(pipeline_id, f"Retrying reconstruction as Nerfstudio job {child_id}")
    return synced_pipeline(pipeline_id)


@app.get("/v1/pipelines/{pipeline_id}/views/{index}", dependencies=[Depends(authenticate)])
def get_pipeline_view(pipeline_id: str, index: int) -> FileResponse:
    metadata = read_pipeline(pipeline_id)
    if index < 0 or index >= metadata.get("generated_views", 0):
        raise HTTPException(404, "Generated view not found")
    path = pipeline_dir(pipeline_id) / "views" / f"{index:03d}.png"
    if not path.is_file():
        raise HTTPException(404, "Generated view not found")
    return FileResponse(path, media_type="image/png", filename=f"view-{index:03d}.png")


@app.get("/v1/pipelines/{pipeline_id}/dataset", dependencies=[Depends(authenticate)])
def get_pipeline_dataset(pipeline_id: str) -> FileResponse:
    path = pipeline_dir(pipeline_id) / "dataset.zip"
    if not path.is_file():
        raise HTTPException(409, "Posed dataset is not ready")
    return FileResponse(path, media_type="application/zip", filename=f"{pipeline_id}-dataset.zip")


@app.get("/v1/pipelines/{pipeline_id}/model", dependencies=[Depends(authenticate)])
def get_pipeline_model(pipeline_id: str) -> FileResponse:
    metadata = synced_pipeline(pipeline_id)
    if metadata["status"] != "complete":
        raise HTTPException(409, f"Pipeline is {metadata['status']}")
    direct_result = pipeline_dir(pipeline_id) / "result.zip"
    if metadata.get("method") == "hunyuan3d" and direct_result.is_file():
        return FileResponse(direct_result, media_type="application/zip", filename=f"{pipeline_id}-model.zip")
    if not metadata.get("nerfstudio_job_id"):
        raise HTTPException(500, "Model result is unavailable")
    result = DATA_ROOT / metadata["nerfstudio_job_id"] / "result.zip"
    if not result.is_file():
        raise HTTPException(500, "Model result is unavailable")
    return FileResponse(result, media_type="application/zip", filename=f"{pipeline_id}-model.zip")


@app.get("/v1/pipelines/{pipeline_id}/log", dependencies=[Depends(authenticate)])
def get_pipeline_log(pipeline_id: str) -> FileResponse:
    path = pipeline_dir(pipeline_id) / "pipeline.log"
    if not path.is_file():
        raise HTTPException(404, "Pipeline log is unavailable")
    return FileResponse(path, media_type="text/plain", filename=f"{pipeline_id}.log")


@app.delete("/v1/pipelines/{pipeline_id}", dependencies=[Depends(authenticate)])
def delete_pipeline(pipeline_id: str) -> dict:
    metadata = synced_pipeline(pipeline_id)
    if metadata["status"] in {"queued", "generating_views", "verifying_views", "preparing_dataset", "training"}:
        raise HTTPException(409, "An active pipeline cannot be deleted")
    child_id = metadata.get("nerfstudio_job_id")
    if child_id and (DATA_ROOT / child_id).is_dir():
        shutil.rmtree(DATA_ROOT / child_id)
    shutil.rmtree(pipeline_dir(pipeline_id))
    return {"deleted": pipeline_id}


@app.post("/v1/jobs", response_model=Job, status_code=202, dependencies=[Depends(authenticate)])
async def create_job(
    dataset: Annotated[UploadFile, File(description="ZIP containing images or a Nerfstudio dataset")],
    method: Annotated[str, Form()] = "nerfacto",
    iterations: Annotated[int, Form(ge=100, le=100000)] = 30000,
    output_type: Annotated[Literal["pointcloud", "model"], Form()] = "pointcloud",
) -> dict:
    if method not in ALLOWED_METHODS:
        raise HTTPException(422, f"method must be one of {sorted(ALLOWED_METHODS)}")
    job_id = str(uuid.uuid4())
    directory = DATA_ROOT / job_id
    directory.mkdir(parents=True)
    upload_path = directory / "dataset.zip"
    size = 0
    try:
        with upload_path.open("wb") as output:
            while chunk := await dataset.read(8 * 1024**2):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "Upload exceeds the configured limit")
                output.write(chunk)
        if not zipfile.is_zipfile(upload_path):
            raise HTTPException(422, "dataset must be a valid ZIP archive")
        timestamp = now()
        metadata = {
            "id": job_id,
            "status": "queued",
            "method": method,
            "iterations": iterations,
            "output_type": output_type,
            "created_at": timestamp,
            "updated_at": timestamp,
            "error": None,
            "result_ready": False,
        }
        (directory / "job.json").write_text(json.dumps(metadata, indent=2))
        await queue.put(job_id)
        return metadata
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    finally:
        await dataset.close()


@app.get("/v1/jobs/{job_id}", response_model=Job, dependencies=[Depends(authenticate)])
def get_job(job_id: str) -> dict:
    return read_job(job_id)


@app.get("/v1/jobs/{job_id}/result", dependencies=[Depends(authenticate)])
def get_result(job_id: str) -> FileResponse:
    metadata = read_job(job_id)
    if metadata["status"] != "complete":
        raise HTTPException(409, f"Job is {metadata['status']}")
    result = job_dir(job_id) / "result.zip"
    if not result.is_file():
        raise HTTPException(500, "Result archive is unavailable")
    return FileResponse(result, media_type="application/zip", filename=f"{job_id}.zip")


@app.get("/v1/jobs/{job_id}/log", dependencies=[Depends(authenticate)])
def get_log(job_id: str) -> FileResponse:
    log = job_dir(job_id) / "job.log"
    if not log.is_file():
        raise HTTPException(404, "Log is not available yet")
    return FileResponse(log, media_type="text/plain", filename=f"{job_id}.log")


@app.delete("/v1/jobs/{job_id}", dependencies=[Depends(authenticate)])
def delete_job(job_id: str) -> dict:
    metadata = read_job(job_id)
    if metadata["status"] in {"processing", "training", "exporting"}:
        raise HTTPException(409, "A running job cannot be deleted")
    shutil.rmtree(job_dir(job_id))
    return {"deleted": job_id}


@app.get("/", include_in_schema=False)
def dashboard_redirect() -> RedirectResponse:
    return RedirectResponse(url="/dashboard/")


if FRONTEND_ROOT.is_dir():
    app.mount("/dashboard", StaticFiles(directory=FRONTEND_ROOT, html=True), name="dashboard")
