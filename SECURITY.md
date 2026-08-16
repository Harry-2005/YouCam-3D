# Security policy

## Reporting a vulnerability

Please do not open a public issue for authentication bypasses, credential exposure, unsafe archive handling, or access to private user images. Use GitHub's private vulnerability reporting for this repository.

Include a concise impact statement, affected endpoint or component, reproduction steps, and any suggested mitigation. Do not include real credentials or private user media.

## Supported version

Security fixes target the latest commit on `main`.

## Deployment guidance

- Keep the API behind TLS and require bearer authentication for every non-health route.
- Store YouCam credentials and service identity only on the server.
- Rotate the application bearer token after suspected exposure.
- Restrict `/var/lib/nerfstudio-api` and `/opt/nerfstudio-api` to the service account.
- Apply host, container, CUDA, and Python dependency updates regularly.
