# Contributing

Thanks for helping improve YouCam 3D.

## Development workflow

1. Fork the repository and branch from `main`.
2. Keep changes focused and use descriptive, imperative commit messages.
3. Do not commit credentials, generated models, job data, or browser profiles.
4. Run the local checks before opening a pull request:

   ```bash
   python -m py_compile deploy/*.py
   node --check frontend/app.js
   git diff --check
   ```

5. In the pull request, describe the user-visible behavior, operational impact, and how the change was verified.

## Pull request scope

- Keep API behavior backward-compatible where practical.
- Document new environment variables and endpoints.
- Include screenshots for meaningful dashboard changes.
- Keep generated-view quality changes separate from reconstruction changes so they can be evaluated independently.
- Never weaken identity or archive-safety checks without a documented replacement.

## Reporting bugs

Include the pipeline status, reconstruction method, relevant sanitized log excerpt, GPU type, and reproduction steps. Never attach bearer tokens, YouCam credentials, service-account keys, or unredacted user photos.
