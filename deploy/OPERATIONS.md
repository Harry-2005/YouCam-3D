# Operations

- GCP project: `gen-lang-client-0663392387`
- VM: `nerfstudio-a100`
- Zone: `asia-southeast1-c`
- Machine type: `a2-highgpu-1g` (one NVIDIA A100 40 GB)
- Static address: `34.143.145.140`
- API endpoint: `https://34.143.145.140.nip.io`
- Local bearer-token file: `.nerfstudio-api-token` (ignored by Git)
- VM service account: `my-product-sa@gen-lang-client-0663392387.iam.gserviceaccount.com`
- Nano Banana billing project: `project-a2dcdad0-5d65-4d61-846`
- Nano Banana model: `gemini-3.1-flash-image` through Vertex AI `global`
- Orbit model: `veo-3.1-fast-generate-001` (8 seconds, 720p, no audio)
- Veo output bucket: `gs://youcam-parallax-project-a2dcdad0-5d65-4d61-846`
- Dashboard: `https://34.143.145.140.nip.io/dashboard/`
- Cloudflare Quick Tunnel: `https://letter-combo-vocabulary-geek.trycloudflare.com`
- Tunnel container: `parallax-cloudflared` (`--restart unless-stopped`)
- Preferred pipeline coherence: `0.80` (override with `PIPELINE_COHERENCE_THRESHOLD`).
- Imperfect view sets continue with a warning when coherence is at least `0.40`, identity is at least `0.75`, and camera coverage is valid. Override the floors with `PIPELINE_MIN_USABLE_COHERENCE` and `PIPELINE_MIN_IDENTITY_SCORE`.
- Pose stabilization: up to 2 repair-and-re-audit passes (override with
  `PIPELINE_STABILIZATION_PASSES`; model via `NANO_BANANA_STABILIZER_MODEL`)

The VM is on-demand and continues billing while it is running. Stop it when the
API is not needed; the persistent disk, static IP, and data remain in place.

The `trycloudflare.com` hostname is a development Quick Tunnel. It persists
while the current container invocation survives, but Cloudflare can assign a
new random hostname after the container is recreated. A stable branded hostname
requires a Cloudflare account, managed domain, and tunnel token.

```powershell
gcloud compute instances stop nerfstudio-a100 `
  --zone=asia-southeast1-c `
  --project=gen-lang-client-0663392387

gcloud compute instances start nerfstudio-a100 `
  --zone=asia-southeast1-c `
  --project=gen-lang-client-0663392387
```

Inspect the service or a job log:

```powershell
gcloud compute ssh nerfstudio-a100 `
  --zone=asia-southeast1-c `
  --project=gen-lang-client-0663392387 `
  --command="sudo systemctl status nerfstudio-api caddy"
```

Pipeline artifacts live in `/var/lib/nerfstudio-api/pipelines/<pipeline-id>`;
the linked Nerfstudio jobs live in `/var/lib/nerfstudio-api/jobs/<job-id>`. The
generation stage stores its closed-orbit MP4 locally and retains the Vertex
output in the bucket for traceability. Low-scoring captures stop before GPU
training. Each capture requests eight seconds of Veo video, so budget Vertex
video charges separately from A100 runtime.

Rotate the bearer token on the VM, then retrieve the new value securely:

```bash
sudo sed -i "s/^API_KEY=.*/API_KEY=$(openssl rand -hex 32)/" /opt/nerfstudio-api/.env
sudo systemctl restart nerfstudio-api
```
