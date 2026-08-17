#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl debian-keyring debian-archive-keyring apt-transport-https ffmpeg openssl
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
  | gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
  > /etc/apt/sources.list.d/caddy-stable.list
chmod 0644 /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list
apt-get update
apt-get install -y caddy

install -d -m 0750 -o nerfstudio-api -g nerfstudio-api \
  /opt/nerfstudio-api /var/lib/nerfstudio-api/jobs /var/lib/nerfstudio-api/cache \
  /var/lib/nerfstudio-api/pipelines /var/lib/nerfstudio-api/style-jobs
install -m 0644 /tmp/nerfstudio-deploy/app.py /opt/nerfstudio-api/app.py
install -m 0644 /tmp/nerfstudio-deploy/requirements.txt /opt/nerfstudio-api/requirements.txt
if [[ -d /tmp/nerfstudio-deploy/frontend ]]; then
  rm -rf /opt/nerfstudio-api/frontend
  cp -a /tmp/nerfstudio-deploy/frontend /opt/nerfstudio-api/frontend
  chown -R root:root /opt/nerfstudio-api/frontend
fi
/opt/nerfstudio-api/venv/bin/pip install -r /opt/nerfstudio-api/requirements.txt

if [[ ! -s /opt/nerfstudio-api/.env ]]; then
  umask 0077
  printf 'API_AUTH_REQUIRED=false\nDATA_ROOT=/var/lib/nerfstudio-api/jobs\nCACHE_ROOT=/var/lib/nerfstudio-api/cache\nPIPELINE_ROOT=/var/lib/nerfstudio-api/pipelines\nSTYLE_ROOT=/var/lib/nerfstudio-api/style-jobs\nYOUCAM_API_KEY_FILE=/opt/nerfstudio-api/.youcam-api-key\nNERFSTUDIO_IMAGE=ghcr.io/nerfstudio-project/nerfstudio:latest\nNANO_BANANA_PROJECT=project-a2dcdad0-5d65-4d61-846\nNANO_BANANA_LOCATION=global\nNANO_BANANA_MODEL=gemini-3.1-flash-image\nVEO_MODEL=veo-3.1-fast-generate-001\nVEO_OUTPUT_BUCKET=youcam-parallax-project-a2dcdad0-5d65-4d61-846\nNANO_BANANA_STABILIZER_MODEL=gemini-3.1-flash-image\nPIPELINE_STABILIZATION_PASSES=2\nPIPELINE_MIN_USABLE_COHERENCE=0.40\nPIPELINE_MIN_IDENTITY_SCORE=0.75\n' > /opt/nerfstudio-api/.env
fi
chown root:nerfstudio-api /opt/nerfstudio-api/.env
chmod 0640 /opt/nerfstudio-api/.env
chown -R nerfstudio-api:nerfstudio-api /var/lib/nerfstudio-api

install -m 0644 /tmp/nerfstudio-deploy/nerfstudio-api.service /etc/systemd/system/nerfstudio-api.service
install -m 0644 /tmp/nerfstudio-deploy/Caddyfile /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl daemon-reload
systemctl enable --now nerfstudio-api.service
systemctl enable --now caddy.service
