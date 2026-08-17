# Deploying to Railway

Deploys the Streamlit app as a public web service that calls the Claude API with your
key from the Anthropic console.

---

## 1. Get an Anthropic API key

1. Sign in at <https://console.anthropic.com/>.
2. **API keys → Create key**. Copy it — the console shows it once.
3. Make sure the workspace has credit (**Billing → Plans**). A key with no credit
   authenticates fine and then fails on the first call.

The key is read from the `ANTHROPIC_API_KEY` environment variable only. It is never in
source, never written to an output file, and the logger redacts anything credential-shaped
before it is emitted. All three properties are covered by tests in
[`tests/test_deployment.py`](tests/test_deployment.py).

---

## 2. Push the project to GitHub

```bash
cd ExecutiveSummary
git init
git add .
git commit -m "Investor executive summary generator"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

`.gitignore` already excludes `.env`, `input/`, and `output/`, so no key and no
confidential deck is committed. `templates/` **is** committed — the app needs the
canonical structure at runtime.

Confirm before pushing:

```bash
git status --porcelain          # .env must not appear
git ls-files | grep -E "input/|output/|\.env$"   # should print nothing
```

---

## 3. Create the Railway service

1. <https://railway.app> → **New Project → Deploy from GitHub repo**.
2. Pick the repository. Railway detects the Python app and reads
   [`railway.json`](railway.json) and [`nixpacks.toml`](nixpacks.toml).
3. It builds automatically. The first build takes 2–4 minutes.

Committed configuration:

| File | Purpose |
|---|---|
| `railway.json` | Start command, health check on `/_stcore/health`, restart policy |
| `nixpacks.toml` | Python 3.12, dependency install, optional LibreOffice |
| `Procfile` | Same start command, for any Procfile-based host |
| `.python-version` | Pins Python 3.12 |
| `.streamlit/config.toml` | Upload limits, XSRF protection, theme, no usage stats |

---

## 4. Set environment variables

**Variables** tab → **New Variable**:

| Variable | Value | Required |
|---|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-…` | Yes, for the AI pipeline |
| `APP_PASSWORD` | a long random passphrase | Strongly recommended |
| `ALLOW_USER_API_KEY` | `true` | Optional |
| `REQUIRE_USER_API_KEY` | `true` | Optional |
| `MAX_UPLOAD_MB` | `80` | Optional |

Do **not** set `PORT` — Railway injects it, and the start command reads it.

Saving a variable triggers a redeploy.

### Who pays for the Claude calls

A Railway URL is public. Anyone who reaches it spends **your** Anthropic credit, and a full
run on a 30-slide deck costs roughly 18 API calls.

Three options:

- **`APP_PASSWORD`** — one shared secret, you fund the runs. Best for a small team.
- **`ALLOW_USER_API_KEY=true`** — a key field appears in the UI; visitors may use their own.
- **`REQUIRE_USER_API_KEY=true`** — your key is never offered; every visitor brings one.

Setting neither a password nor a key requirement leaves an open, billable endpoint.

---

## 5. Expose the URL

**Settings → Networking → Generate Domain** produces
`https://<name>.up.railway.app`. Add a custom domain there if you have one.

Railway waits for `/_stcore/health` to return `200 ok` before routing traffic.

---

## 6. Verify

1. Open the URL. With `APP_PASSWORD` set, the gate appears first.
2. Upload a pitch deck (PDF or PPTX). The reference is optional — without one the app uses
   the canonical template in `templates/`.
3. **Generate Executive Summary**.
4. Confirm the sidebar shows **Claude API ready**, then check the Preview tab and the three
   download buttons.

Expect roughly 90 seconds for a 10-slide deck and 5–6 minutes for a 30-slide deck with the
multimodal pass on. Lower **Maximum slides sent to vision** in the sidebar to trade depth
for speed.

---

## PowerPoint chart images

Reading charts that are *images* inside a `.pptx` needs LibreOffice to rasterise slides.
It is off by default because it adds ~500 MB to the image and minutes to each build.

Without it, PowerPoint decks still parse completely — text, tables, native charts, grouped
shapes, and speaker notes. Only chart *pictures* go unread, and the app says so in the logs
rather than guessing at values.

To enable, uncomment one line in `nixpacks.toml`:

```toml
[phases.setup]
nixPkgs = [
  "python312",
  "libreoffice-fresh",   # <- uncomment
]
```

PDF decks never need this: PyMuPDF rasterises them natively.

---

## Resources and cost

The app is memory-bound, not CPU-bound — PyMuPDF holds rendered slides in memory during the
vision pass.

| Setting | Recommendation |
|---|---|
| Memory | 1 GB minimum; 2 GB for 30+ slide decks with vision on |
| Replicas | 1 — runs are stateful within a session |
| Sleep | Fine to enable; the health check wakes the service |

Two costs, billed separately: Railway for hosting, Anthropic per API call.

---

## Storage is ephemeral

Railway containers have no persistent disk, and a redeploy wipes the filesystem. The app
detects Railway automatically and writes generated files to a temp directory instead of
`output/`, delivering them through the download buttons.

Users must download what they need — nothing is retained server-side. That is also the
safer default for confidential decks.

To attach a Railway volume anyway, mount it and set `OUTPUT_DIR` to the mount path.

---

## Troubleshooting

**Build fails on dependency install**
Check the build log for the failing wheel. `requirements.txt` pins compatible ranges for
Python 3.12; if you changed `.python-version`, revert it.

**Health check fails / service restarts**
Almost always the bind address. The start command must include
`--server.address=0.0.0.0 --server.port=$PORT`. Binding localhost makes the container
unreachable from Railway's proxy. `tests/test_deployment.py` asserts both flags.

**"No API key configured — deterministic mode only"**
`ANTHROPIC_API_KEY` is unset or misspelled in the Variables tab. It must be in the same
service as the app, and saving it triggers a redeploy — wait for that to finish.

**Runs start then fail partway**
Usually Anthropic credit exhaustion or rate limiting. The provider retries transient errors
with backoff, then falls back to deterministic composition, so you still get a PDF. Check
deploy logs for `AI call failed`.

**"File too large"**
Raise `MAX_UPLOAD_MB` *and* `maxUploadSize` in `.streamlit/config.toml` — the lower of the
two wins.

**Out of memory on large decks**
Raise the memory limit, or lower **Maximum slides sent to vision**.

**Upload succeeds but the page is sparse**
The deck is image-based with little extractable text. Confirm the API key is set and vision
is enabled; check `native_text_chars` in the extracted-data JSON.

---

## Deploying elsewhere

The app is a standard Streamlit service with no Railway-specific code — only
`RAILWAY_ENVIRONMENT` detection, which `MANAGED_DEPLOYMENT=true` reproduces on any host.

- **Render / Fly.io / Heroku** — the `Procfile` works as-is.
- **Docker** — install `requirements.txt` on `python:3.12-slim` and use the same start
  command.
- **Streamlit Community Cloud** — point it at `app.py` and set `ANTHROPIC_API_KEY` in
  Secrets.
