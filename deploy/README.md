# Deployment guide

The canonical project overview and API examples live in the [root README](../README.md). This document covers the GPU host layout.

## Runtime layout

| Path | Purpose |
| --- | --- |
| `/opt/nerfstudio-api` | Application, virtual environment, frontend, and protected configuration |
| `/var/lib/nerfstudio-api/jobs` | Nerfstudio worker jobs |
| `/var/lib/nerfstudio-api/pipelines` | Twelve-view pipelines and final artifacts |
| `/var/lib/nerfstudio-api/style-jobs` | Provider-routed outfit previews |
| `/var/lib/nerfstudio-api/cache` | Shared model cache |

The FastAPI process runs as `nerfstudio-api` on `127.0.0.1:8000`. Caddy terminates TLS and proxies public traffic. Reconstruction commands run in an NVIDIA-enabled Nerfstudio container; relightable mesh jobs use the dedicated Hunyuan environment.

## Install

From the repository root:

```bash
sudo install -d -m 0755 /tmp/nerfstudio-deploy
sudo cp -a deploy/. /tmp/nerfstudio-deploy/
sudo cp -a frontend /tmp/nerfstudio-deploy/frontend

sudo bash deploy/bootstrap.sh
sudo bash deploy/install.sh
```

Before running `install.sh`, replace the hostname in `deploy/Caddyfile`. After installation, review `/opt/nerfstudio-api/.env`, install the YouCam credential file with mode `0600`, and restart the service.

```bash
sudo install -m 0600 /secure/path/youcam-creds.txt \
  /opt/nerfstudio-api/.youcam-api-key
sudo systemctl restart nerfstudio-api caddy
```

## Add clothing providers

YouCam Clothes v3 is the native provider. Extra clothing APIs can be connected without frontend changes by exposing the provider webhook contract and adding a server-side definition:

```bash
CLOTHING_PROVIDER_ORDER=youcam,studio-fit
CLOTHING_WEBHOOK_PROVIDERS_JSON='[{"id":"studio-fit","label":"Studio Fit","endpoint":"https://gateway.example.com/v1/try-on","token_env":"STUDIO_FIT_TOKEN"}]'
STUDIO_FIT_TOKEN=replace-with-secret
```

The gateway receives a multipart `POST` with `identity_image`, `garment_image`, `garment_category`, and `gender_mode`. It may return an image directly, JSON containing `result_url`, `output_url`, or `image_url`, or a `status_url` that eventually returns one of those fields. Endpoints must use HTTPS and tokens remain server-side.

The dashboard discovers configured engines through `GET /v1/clothing-providers`. Its **Auto · best available** option follows `CLOTHING_PROVIDER_ORDER` and falls through to the next configured engine if one is unavailable, so adding providers does not add another step to the user flow.

## Validate

```bash
curl https://your-domain.example/health
sudo systemctl is-active nerfstudio-api caddy docker
nvidia-smi
```

Run the bundled smoke test after exporting the deployment URL and bearer token:

```bash
export NERFSTUDIO_URL="https://your-domain.example"
export NERFSTUDIO_API_KEY="replace-me"
bash deploy/smoke_test.sh
```

## Upgrade

Copy the new deployment bundle to `/tmp/nerfstudio-deploy`, rerun `install.sh`, and verify `/health`. The installer preserves an existing `.env`.

## Uninstall

Disable the services before removing application files. Pipeline data is intentionally separate under `/var/lib/nerfstudio-api`; retain or remove it according to your data-retention policy.
