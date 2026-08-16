# Nerfstudio A100 API

## Fashion-to-3D flow

The dashboard can create an outfit preview before reconstruction. `POST /v1/style-jobs`
accepts `identity_image`, a public `instagram_url`, an optional `garment_image` fallback,
and `garment_category=auto|upper_body|lower_body|full_body`. Poll
`GET /v1/style-jobs/{id}`; when complete, inspect `/garment` and `/result`, then call
`POST /v1/style-jobs/{id}/approve` to queue the existing 12-view reconstruction.

Person and outfit jobs default to `splatfacto`, which preserves facial appearance and
fine hair/eyewear detail photometrically. `hunyuan3d` remains available as the relightable
mesh option and uses a higher-resolution octree, denser surface, and 2K texture profile.

The YouCam bearer key is read server-side from `YOUCAM_API_KEY_FILE`; it must never be
placed in frontend code. Instagram ingestion supports public reel/post URLs and may need
the fallback image when Instagram requires authentication.

Perfect Corp retains API uploads/tasks for 30 days and generated download links for two
hours. This service downloads successful results immediately. Local preview data can be
removed with `DELETE /v1/style-jobs/{id}`.

The service accepts a ZIP containing at least 10 overlapping scene images (or a
preprocessed Nerfstudio dataset containing `transforms.json`). Jobs run one at a
time on the GPU.

```bash
curl -X POST "$NERFSTUDIO_URL/v1/jobs" \
  -H "Authorization: Bearer $NERFSTUDIO_API_KEY" \
  -F "dataset=@scene-images.zip" \
  -F "method=nerfacto" \
  -F "iterations=30000" \
  -F "output_type=pointcloud"
```

Poll the returned job ID and download the result:

```bash
curl -H "Authorization: Bearer $NERFSTUDIO_API_KEY" \
  "$NERFSTUDIO_URL/v1/jobs/$JOB_ID"

curl -L -H "Authorization: Bearer $NERFSTUDIO_API_KEY" \
  -o result.zip "$NERFSTUDIO_URL/v1/jobs/$JOB_ID/result"
```

Use `output_type=model` to receive the trained Nerfstudio configuration and
checkpoints instead of an exported point cloud. The unauthenticated `/health`
endpoint reports GPU and queue health.

## Capture dashboard

Open `https://34.143.145.140.nip.io` and paste the bearer token from
`.nerfstudio-api-token`. The dashboard accepts 1–8 reference images, a subject
directive, a fixed 12-view closed orbit, and a Nerfstudio training preset.

Nano Banana first combines the references and generated directive into one locked canonical
subject. Veo 3.1 Fast then renders an eight-second closed 360-degree camera
orbit with that canonical image fixed as both the first and last frame. The API
samples 12 full-resolution video frames, audits identity, geometry, pose, and
camera progression. A score of 0.80 remains the preferred target; usable lower-scoring
orbits continue with a quality warning when identity and camera coverage remain valid.
The intended orbit uses a fixed 10-degree elevation and 30-degree azimuth steps
recorded in `transforms.json`; COLMAP is intentionally bypassed for generated
imagery. Veo motion remains generative, so only unusable identity or camera-orbit
audits return no model.

The `prompt` form field is optional. Before queuing a capture, Gemini inspects
all uploaded references and generates a reconstruction-safe subject directive
covering identity, materials, geometry, accessories, and a frozen pose. When
provided, `prompt` is treated as user guidance and incorporated into that
generated directive.

When the semantic audit detects articulated pose drift, the API runs a
rigid-subject stabilization layer before rejecting the capture. Nano Banana
uses the canonical front pose as an immutable body-local pose contract, repairs
the affected target-azimuth frames, and re-audits every accepted repair pass.
The preferred coherence threshold is unchanged; an unsuccessful repair can continue with
a warning when it remains above the minimum usable floor. Severe identity or orbit failures stop before
GPU reconstruction. Configure the repair budget with
`PIPELINE_STABILIZATION_PASSES` (default `2`).

Create a capture without the UI:

```powershell
$base = "https://34.143.145.140.nip.io"
$token = (Get-Content .nerfstudio-api-token).Trim()

curl.exe -X POST "$base/v1/pipelines" `
  -H "Authorization: Bearer $token" `
  -F "references=@C:\path\front.png;type=image/png" `
  -F "references=@C:\path\side.png;type=image/png" `
  -F "prompt=Preserve the exact product, materials, markings, and rigid pose" `
  -F "view_count=12" `
  -F "method=nerfacto" `
  -F "iterations=10000"
```

Poll `/v1/pipelines/{id}`. Authenticated routes under that pipeline expose each
generated view, the posed dataset ZIP, log, and final point-cloud ZIP. View sets
below the preferred coherence target carry a quality warning but continue unless
they fall below the usable floor or lose identity/camera coverage.

## Nano Banana 2

Nano Banana uses Vertex AI through the VM's attached service account. It does
not require an API key or credential JSON on the server. The same bearer token
that protects the Nerfstudio endpoints protects image generation and editing.

Generate an image:

```powershell
$base = "https://34.143.145.140.nip.io"
$token = (Get-Content .nerfstudio-api-token).Trim()

curl.exe -X POST "$base/v1/images/generate" `
  -H "Authorization: Bearer $token" `
  -F "prompt=A studio photograph of a futuristic yellow camera" `
  -F "aspect_ratio=1:1" `
  -F "image_size=1K" `
  -o generated.png
```

Edit or combine one or more reference images:

```powershell
curl.exe -X POST "$base/v1/images/edit" `
  -H "Authorization: Bearer $token" `
  -F "prompt=Keep the subject, replace the background with a cinematic night scene" `
  -F "images=@C:\path\reference.png;type=image/png" `
  -F "aspect_ratio=16:9" `
  -F "image_size=2K" `
  -o edited.png
```

Defaults are `gemini-3.1-flash-image`, `1:1`, and `1K`. Supported model
overrides are `gemini-3.1-flash-lite-image`, `gemini-3-pro-image`, and
`gemini-2.5-flash-image`. Set `response_format=json` to receive response text
and base64-encoded images instead of the first image as a binary response.
