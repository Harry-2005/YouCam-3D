# Deployment guide

The canonical project overview and API examples live in the [root README](../README.md). This document covers the GPU host layout.

## Runtime layout

| Path | Purpose |
| --- | --- |
| `/opt/nerfstudio-api` | Application, virtual environment, frontend, and protected configuration |
| `/var/lib/nerfstudio-api/jobs` | Nerfstudio worker jobs |
| `/var/lib/nerfstudio-api/pipelines` | Twelve-view pipelines and final artifacts |
| `/var/lib/nerfstudio-api/style-jobs` | YouCam outfit previews |
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
