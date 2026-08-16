#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y ca-certificates curl docker.io python3-venv
systemctl enable --now docker

curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | gpg --batch --yes --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  > /etc/apt/sources.list.d/nvidia-container-toolkit.list
apt-get update
apt-get install -y nvidia-container-toolkit
nvidia-ctk runtime configure --runtime=docker
systemctl restart docker

install -d -m 0750 /opt/nerfstudio-api /var/lib/nerfstudio-api/jobs
id nerfstudio-api >/dev/null 2>&1 || useradd --system --home /opt/nerfstudio-api --shell /usr/sbin/nologin nerfstudio-api
usermod -aG docker nerfstudio-api
chown -R nerfstudio-api:nerfstudio-api /opt/nerfstudio-api /var/lib/nerfstudio-api

python3 -m venv /opt/nerfstudio-api/venv
/opt/nerfstudio-api/venv/bin/pip install --upgrade pip

# Caddy is installed from its official Debian repository after app files are copied.
docker pull ghcr.io/nerfstudio-project/nerfstudio:latest
