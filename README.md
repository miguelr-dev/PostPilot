# PostPilot 🛩️

**LinkedIn posts that sound like you, powered by live AI news.**

Paste a meeting transcript or your notes. PostPilot learns your voice — your themes, opinions, and writing style — pulls the latest AI headlines from news feeds, Hacker News, and arXiv, and drafts ready-to-publish LinkedIn posts that react to the news *as you*. Each draft comes with a license-safe stock image, complete with attribution.

## Features

- **Live news, not stale takes** — gathers fresh AI stories within a configurable lookback window (days, months, or years)
- **Your actual voice** — builds a voice profile from a transcript or notes and ghostwrites in your style, not generic AI-speak
- **License-safe images** — every draft gets a matching stock photo from Openverse or Pexels with license and attribution details
- **Fully tunable** — tone (insightful / casual / bold / technical), target length from 0–3,000 words, 1–5 variants per run
- **Web UI + CLI** — use the browser app with live progress, or run `generator.py` straight from the terminal

## Quick start

Requires Python 3.10+.

```bash
git clone https://github.com/YOUR_USERNAME/postpilot.git
cd postpilot
pip install -r requirements.txt
python app.py
```

Open **http://localhost:5000** — the landing page is at `/`, the generator at `/app`.

### API keys (optional but recommended)

Set these as environment variables before starting the server:

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | AI-written posts and voice profiling. Without it, PostPilot runs in **template mode** (real news, basic fill-in templates). |
| `TAVILY_API_KEY` | Better news search (falls back to RSS feeds without it) |
| `PEXELS_API_KEY` | Better stock images (falls back to Openverse without it) |
| `LLM_MODEL` | Override the Claude model (default: `claude-sonnet-4-5`) |
| `PORT` | Change the web server port (default: 5000) |
| `LINKEDIN_CLIENT_ID` / `LINKEDIN_CLIENT_SECRET` | From your LinkedIn developer app — enables "Connect LinkedIn" and one-click posting |
| `BASE_URL` | Public URL of the site (e.g. `https://postpilot-nsks.onrender.com`), used for the OAuth redirect. Default: `http://localhost:5000` |
| `SECRET_KEY` | Random string for login sessions (generate one; without it, logins reset on every server restart) |
| `DATABASE_URL` | Postgres connection string. When set, the drafts library uses Postgres (persistent, survives deploys); without it, a local SQLite file is used. Libraries are per-user: drafts belong to the LinkedIn account connected when saving. |

PowerShell example:

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
python app.py
```

⚠️ Never hardcode keys into the source files or commit them — environment variables only.

## Usage

1. Paste a transcript, voice memo transcription, or rough notes into the generator (or tick "Use built-in sample transcript" to try it out)
2. Pick tone, word count, number of variants, and news lookback
3. Hit **Generate posts** and watch the progress: gathering news → building your voice profile → writing variants → finding images
4. Copy the draft you like into LinkedIn and attach its image

You can also add voice material as `.txt` files in `data/past_posts/` and `data/notes/` — they're picked up automatically on every run.

### CLI

The original single-file generator still works standalone:

```bash
python generator.py --tone bold --length short --variants 3 --lookback 48
```

Results are saved to `output/posts-<timestamp>.md` with images in `output/images/`.

## Project structure

```
app.py            Flask backend — background jobs, progress polling, static serving
generator.py      Core engine — news gathering, voice profiling, post generation, image sourcing
index.html        Landing page (/)
generator.html    Generator app (/app)
app.js            Frontend logic
styles.css        Shared styles
requirements.txt  Python dependencies
```

## API

| Endpoint | Description |
|---|---|
| `POST /api/generate` | Start a job. Body: `{transcript, use_sample, tone, word_count, variants, lookback_value, lookback_unit, images}` → `{job_id}` |
| `GET /api/status/<job_id>` | Poll progress: `{status, progress, result, error}`. On success, `result.drafts[]` contains `text`, `source`, and `image` |
| `GET /api/health` | Which API keys are configured |

## Notes & limitations

- LinkedIn caps posts at ~3,000 characters (roughly 450 words) — longer drafts work better as LinkedIn articles
- News sources only expose recent items, so long lookbacks mean "everything currently in the feeds," not a true archive
- The built-in Flask dev server is fine locally; for public deployment use `waitress` or `gunicorn`, and add rate limiting — each generation costs API calls
- Jobs are held in memory; restarting the server clears them
- Drafts are suggestions — always review before posting

## License

MIT — do whatever you like, no warranty. Stock images retain their own licenses (Pexels License or Creative Commons via Openverse); attribution details are provided with each image.
