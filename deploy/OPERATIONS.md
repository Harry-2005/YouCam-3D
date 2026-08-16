# Operations

## Service checks

```bash
sudo systemctl status nerfstudio-api caddy docker
sudo journalctl -u nerfstudio-api -n 150 --no-pager
nvidia-smi
curl -fsS https://your-domain.example/health
```

## Job locations

- Pipeline metadata and logs: `/var/lib/nerfstudio-api/pipelines/<pipeline-id>`
- Linked Nerfstudio jobs: `/var/lib/nerfstudio-api/jobs/<job-id>`
- YouCam preview jobs: `/var/lib/nerfstudio-api/style-jobs/<style-id>`

The API processes GPU jobs through a queue. A running job may use substantial GPU memory; unchanged queue state is normal while a long reconstruction is active.

## Quality controls

- Preferred coherence: `PIPELINE_COHERENCE_THRESHOLD=0.80`
- Minimum usable coherence: `PIPELINE_MIN_USABLE_COHERENCE=0.40`
- Minimum identity score: `PIPELINE_MIN_IDENTITY_SCORE=0.75`
- Stabilization passes: `PIPELINE_STABILIZATION_PASSES=2`
- Parallel repair workers: `PIPELINE_STABILIZATION_WORKERS=4`

Minor cosmetic inconsistencies can continue with a warning. Identity loss, invalid camera coverage, or incomplete views remain hard failures.

## Rotate the application token

```bash
NEW_TOKEN="$(openssl rand -hex 32)"
sudo sed -i "s/^API_KEY=.*/API_KEY=${NEW_TOKEN}/" /opt/nerfstudio-api/.env
sudo systemctl restart nerfstudio-api
unset NEW_TOKEN
```

Store the new token in a password manager. Never place it in a shell history, frontend bundle, issue, or pull request.

## Backup and retention

Back up `/var/lib/nerfstudio-api` only when retaining user jobs is required. Treat person images, outfit sources, orbit frames, and 3D models as private user data. Apply a documented retention period and remove expired jobs through the authenticated API or a reviewed maintenance process.

## GPU host lifecycle

When the service is idle, stop the GPU instance through your cloud provider to avoid compute charges. Persistent disks and reserved addresses may continue billing while the instance is stopped.
