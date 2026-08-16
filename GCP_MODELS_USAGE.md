# Using GCP Vertex AI Models — Your Setup

Verified working **2026-06-08**. This is specific to your machine and accounts. Follow exactly.

---

## Reference values (every concrete value, in one place)

| Key | Value |
|---|---|
| ADC account (for model calls) | `project.anubis.labs@gmail.com` |
| gcloud CLI account (admin only, do not use for models) | `fanwiser1@gmail.com` |
| Billing account (active, owns both projects below) | `018A84-514A6F-31A92E` |
| **Claude project ID** | `gen-lang-client-0663392387` |
| Claude project number | `939419364980` |
| Claude project display name | `Default Gemini Project` |
| Claude model ID | `claude-sonnet-4-6` |
| Claude location | `global` |
| Claude endpoint host | `aiplatform.googleapis.com` |
| Claude methods | `rawPredict`, `streamRawPredict` |
| Claude anthropic_version | `vertex-2023-10-16` |
| **Gemini project ID** | `project-a2dcdad0-5d65-4d61-846` |
| Gemini project number | `656993776304` |
| Gemini project display name | `My First Project` |
| Gemini model IDs | `gemini-2.5-pro`, `gemini-2.5-flash`, `gemini-2.5-flash-lite` |
| Gemini location / region | `us-central1` |
| Gemini endpoint host | `us-central1-aiplatform.googleapis.com` |
| Gemini methods | `generateContent`, `streamGenerateContent` |
| ADC quota project (currently set) | `project-a2dcdad0-5d65-4d61-846` |
| Token command | `gcloud auth application-default print-access-token` |
| Dead project (closed billing — ignore) | `claude-vertex-473710` (acct `011A2F-95E9C9-D0843B`, closed) |

Full Claude URL:
`https://aiplatform.googleapis.com/v1/projects/gen-lang-client-0663392387/locations/global/publishers/anthropic/models/claude-sonnet-4-6:rawPredict`

Full Gemini URL (flash):
`https://us-central1-aiplatform.googleapis.com/v1/projects/project-a2dcdad0-5d65-4d61-846/locations/us-central1/publishers/google/models/gemini-2.5-flash:generateContent`

---

## 0. The one thing that trips everything up: accounts

Your machine has **two** Google identities wired in. They are NOT interchangeable.

| Credential store | Account | Used by |
|---|---|---|
| **ADC** (application-default) | `project.anubis.labs@gmail.com` ✅ | **All model API calls** (client libraries + the token below) |
| gcloud CLI (active) | `fanwiser1@gmail.com` ❌ | `gcloud` admin commands only; its billing account is **closed** — useless for models |

**Rule:** model calls must authenticate with the **ADC** token. Get it with:

```bash
gcloud auth application-default print-access-token
```

> Do NOT use `gcloud auth print-access-token` (no `application-default`) for routine work — that returns the `fanwiser1` CLI token. (It happened to work for the global Claude endpoint, but ADC is the correct, reliable identity.)

If ADC ever breaks/expires, re-login (interactive, opens browser) and pick **`project.anubis.labs@gmail.com`**:

```bash
gcloud auth application-default login
gcloud auth application-default set-quota-project project-a2dcdad0-5d65-4d61-846
```

---

## 1. Which model lives in which project

| Model | Project ID | Location | Endpoint host | Method |
|---|---|---|---|---|
| **Claude Sonnet 4.6** | `gen-lang-client-0663392387` | `global` | `aiplatform.googleapis.com` | `rawPredict` / `streamRawPredict` |
| **Gemini 2.5 pro / flash / flash-lite** | `project-a2dcdad0-5d65-4d61-846` | `us-central1` | `us-central1-aiplatform.googleapis.com` | `generateContent` / `streamGenerateContent` |

Both projects have billing enabled (billing account `018A84-514A6F-31A92E`, owned by `project.anubis.labs`).

---

## 2. Claude Sonnet 4.6 (Anthropic on Vertex)

- **Model ID:** `claude-sonnet-4-6`
  - ⚠️ The Model Garden catalog *lists* only `claude-sonnet-4-5@20250929`, but the working ID is **`claude-sonnet-4-6`** (no version suffix). Don't trust the catalog list here.
- **Location must be `global`** — regional hosts (`us-east5`, `us-central1`, `europe-west1`) all return **404**.
- Request body uses the Anthropic Messages schema with `"anthropic_version": "vertex-2023-10-16"` and **no `model` field in the body** (the model is in the URL).

### curl — non-streaming

```bash
TKN=$(gcloud auth application-default print-access-token)
PROJECT="gen-lang-client-0663392387"

curl -s -X POST \
  "https://aiplatform.googleapis.com/v1/projects/${PROJECT}/locations/global/publishers/anthropic/models/claude-sonnet-4-6:rawPredict" \
  -H "Authorization: Bearer ${TKN}" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{
    "anthropic_version": "vertex-2023-10-16",
    "messages": [{"role": "user", "content": "Send me a recipe for banana bread."}],
    "max_tokens": 1024
  }'
```

Response text is at `content[0].text`. Token usage at `usage` (`input_tokens` / `output_tokens`).

### curl — streaming

Same as above but use `:streamRawPredict` and add `"stream": true` to the body. Output is SSE (`event:` / `data:` lines); the text arrives in `content_block_delta` events.

### Python — Anthropic Vertex SDK (recommended)

```bash
pip install "anthropic[vertex]"
```

```python
from anthropic import AnthropicVertex

# Uses ADC automatically (project.anubis.labs). region="global" is required for sonnet-4-6.
client = AnthropicVertex(project_id="gen-lang-client-0663392387", region="global")

msg = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Send me a recipe for banana bread."}],
)
print(msg.content[0].text)
```

---

## 3. Gemini 2.5 (Google on Vertex)

- **Working model IDs:** `gemini-2.5-pro`, `gemini-2.5-flash`, `gemini-2.5-flash-lite`.
- **Not available** (return 404): `gemini-1.5-*` (retired), `gemini-2.0-*` (not enabled in this project).
- ⚠️ **2.5 models spend "thinking" tokens before producing output.** Set `maxOutputTokens` to **≥ 200** or the visible text comes back empty (e.g. `gemini-2.5-pro` used 957 thinking tokens in one test). Check `usageMetadata.thoughtsTokenCount`.

### curl

```bash
TKN=$(gcloud auth application-default print-access-token)
PROJECT="project-a2dcdad0-5d65-4d61-846"
REGION="us-central1"
MODEL="gemini-2.5-flash"

curl -s -X POST \
  "https://${REGION}-aiplatform.googleapis.com/v1/projects/${PROJECT}/locations/${REGION}/publishers/google/models/${MODEL}:generateContent" \
  -H "Authorization: Bearer ${TKN}" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "Explain recursion in 2 sentences."}]}],
    "generationConfig": {"maxOutputTokens": 800}
  }'
```

Response text is at `candidates[0].content.parts[0].text`. Confirm `candidates[0].finishReason == "STOP"`.

### Python — google-genai SDK (recommended)

```bash
pip install google-genai
```

```python
from google import genai

# Vertex mode uses ADC automatically.
client = genai.Client(
    vertexai=True,
    project="project-a2dcdad0-5d65-4d61-846",
    location="us-central1",
)

resp = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="Explain recursion in 2 sentences.",
    config={"max_output_tokens": 800},
)
print(resp.text)
```

---

## 4. Quick health check

```bash
# 1. Confirm ADC is the right account (should print: project.anubis.labs@gmail.com)
curl -s "https://www.googleapis.com/oauth2/v1/userinfo?access_token=$(gcloud auth application-default print-access-token)" | python -c "import sys,json;print(json.load(sys.stdin)['email'])"

# 2. Claude ping (expect: OK)
TKN=$(gcloud auth application-default print-access-token)
curl -s -X POST "https://aiplatform.googleapis.com/v1/projects/gen-lang-client-0663392387/locations/global/publishers/anthropic/models/claude-sonnet-4-6:rawPredict" \
  -H "Authorization: Bearer ${TKN}" -H "Content-Type: application/json" \
  -d '{"anthropic_version":"vertex-2023-10-16","messages":[{"role":"user","content":"Reply with exactly: OK"}],"max_tokens":20}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['content'][0]['text'])"
```

---

## 5. Troubleshooting cheat-sheet

| Symptom | Cause | Fix |
|---|---|---|
| `403 ... requires billing to be enabled` | You're hitting project `claude-vertex-473710` (fanwiser1, closed billing) | Use the projects in §1, with ADC token |
| `404 ... model was not found or your project does not have access` (Claude) | Wrong location or wrong model ID | Use `global` + `claude-sonnet-4-6` |
| Claude works but you used wrong project | Claude is enabled on `gen-lang-client-0663392387` only | Use that project for Claude |
| Gemini returns empty text, `finishReason: STOP` | Thinking tokens ate the budget | Raise `maxOutputTokens` to ≥ 200 |
| `Service Usage API has not been used...` | That call needs Service Usage API; not needed for inference | Ignore for model calls |
| Token expired / 401 | ADC token is short-lived | Re-run `gcloud auth application-default print-access-token` (or re-login per §0) |

---

## 5b. Production auth: service account (recommended for your product)

API keys only work for the Gemini Developer API (and that project's prepay credits are depleted). For a real product use a **service account** with Vertex AI — it bills via the working pay-as-you-go account and serves **both** Gemini and Claude.

Create it in **Cloud Shell** (which runs as `project_anubis_labs`, the account with access — your local gcloud CLI is `fanwiser1` and can't):

```bash
gcloud iam service-accounts create my-product-sa \
  --project=gen-lang-client-0663392387 --display-name="My Product SA"

SA="my-product-sa@gen-lang-client-0663392387.iam.gserviceaccount.com"

# Vertex access on BOTH projects (Claude in gen-lang-client, Gemini in project-a2dcdad0)
gcloud projects add-iam-policy-binding gen-lang-client-0663392387 \
  --member="serviceAccount:$SA" --role="roles/aiplatform.user"
gcloud projects add-iam-policy-binding project-a2dcdad0-5d65-4d61-846 \
  --member="serviceAccount:$SA" --role="roles/aiplatform.user"

gcloud iam service-accounts keys create my-product-sa-key.json --iam-account=$SA
cloudshell download my-product-sa-key.json     # save it to your machine, keep it secret
```

Use it (SDKs auto-read the env var; on Cloud Run/GKE/Compute the attached SA is used with no file):

```bash
pip install google-genai "anthropic[vertex]"
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/my-product-sa-key.json"   # PS: $env:GOOGLE_APPLICATION_CREDENTIALS="...json"
python vertex_app.py
```

`vertex_app.py` (in this folder) calls Gemini and Claude through this credential. **Never commit the JSON** (add `*.json` to `.gitignore`); never put it in a frontend.

---

## 6. One-line summary

> Authenticate as **ADC = project.anubis.labs**, call **`claude-sonnet-4-6`** at **`global`** in project **`gen-lang-client-0663392387`**, and **`gemini-2.5-*`** at **`us-central1`** in project **`project-a2dcdad0-5d65-4d61-846`**.
