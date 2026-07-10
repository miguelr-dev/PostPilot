#!/usr/bin/env python3
"""Web app for the LinkedIn Post Generator.

Run:  pip install flask requests feedparser anthropic
      python app.py
Then open http://localhost:5000

Env (optional): ANTHROPIC_API_KEY (AI-written posts), TAVILY_API_KEY,
PEXELS_API_KEY, LLM_MODEL, PORT.
"""
import os
import threading
import uuid

from flask import Flask, jsonify, request, send_from_directory

import generator as g

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=None)

# In-memory job store: jobs run in a thread so the browser can poll progress
# instead of holding one long request open.
JOBS = {}  # id -> {"status", "progress", "result", "error"}
JOBS_LOCK = threading.Lock()


def _job_update(job_id, **kw):
    with JOBS_LOCK:
        JOBS[job_id].update(kw)


def _serialize_draft(text, topic, img):
    d = {
        "text": text,
        "source": {
            "title": topic.primary.title,
            "url": topic.primary.url,
            "date": topic.primary.date,
            "kind": topic.primary.kind,
        },
        "image": None,
    }
    if img:
        local = img.get("local_path") or ""
        d["image"] = {
            # Serve downloaded images through /images/<file>; fall back to remote URL.
            "src": f"/images/{os.path.basename(local)}" if local else img.get("url", ""),
            "source": img.get("source", ""),
            "license": img.get("license", ""),
            "attribution": img.get("attribution", ""),
            "page": img.get("page", ""),
            "query": img.get("query", ""),
        }
    return d


def _run_job(job_id, settings, transcript_text):
    try:
        _job_update(job_id, progress="Gathering live AI news...")
        content, voice_signals = g.gather_signals(settings, transcript_text)
        if not content:
            _job_update(job_id, status="error",
                        error="No news signals found. Check the server's internet "
                              "connection and that feedparser is installed.")
            return

        _job_update(job_id, progress="Building your voice profile...")
        voice = g.build_voice_profile(voice_signals)

        _job_update(job_id, progress="Selecting topics...")
        topics = g.select_topics(content, settings.variants)

        drafts = []
        for i, topic in enumerate(topics, 1):
            _job_update(job_id, progress=f"Writing variant {i} of {len(topics)}...")
            text = g.format_post(g.generate_post(topic, voice, settings),
                                 max_chars=getattr(settings, "max_chars", 2900))
            img = None
            if settings.images:
                try:
                    _job_update(job_id, progress=f"Finding image for variant {i}...")
                    q, _alt = g.build_image_query(topic)
                    img = g.find_image(q, i)
                except Exception as e:
                    print(f"  [warn] image step failed ({e}).")
            drafts.append(_serialize_draft(text, topic, img))

        result = {
            "drafts": drafts,
            "voice_profile": {
                "summary": voice.summary,
                "themes": voice.themes,
                "opinions": voice.opinions,
                "style_notes": voice.style_notes,
            },
            "llm_used": g.llm_available(),
            "settings": {"tone": settings.tone, "length": settings.length,
                         "variants": settings.variants},
        }
        _job_update(job_id, status="done", result=result, progress="Done")
    except Exception as e:
        _job_update(job_id, status="error", error=str(e))


# ----------------------------- API ----------------------------------------

@app.post("/api/generate")
def api_generate():
    data = request.get_json(silent=True) or {}
    tone = data.get("tone", "insightful")
    if tone not in g.TONE_GUIDE:
        tone = "insightful"

    # Target word count: 0-3000 (0 = as short as possible)
    try:
        word_count = max(0, min(3000, int(data.get("word_count", 150))))
    except (TypeError, ValueError):
        word_count = 150
    length_key = f"custom-{word_count}"
    if word_count == 0:
        g.LENGTH_GUIDE[length_key] = "as short as possible - a single punchy line."
    else:
        g.LENGTH_GUIDE[length_key] = (f"about {word_count} words. Aim close to "
                                      f"this word count.")

    try:
        variants = max(1, min(5, int(data.get("variants", 3))))
    except (TypeError, ValueError):
        variants = 3

    # Lookback: value + unit (days/months/years) -> hours
    unit_hours = {"days": 24, "months": 24 * 30, "years": 24 * 365}
    unit = data.get("lookback_unit", "days")
    if unit not in unit_hours:
        unit = "days"
    try:
        lb_value = max(1, int(data.get("lookback_value", 2)))
    except (TypeError, ValueError):
        lb_value = 2
    lookback = max(1, min(24 * 365 * 10, lb_value * unit_hours[unit]))

    transcript = (data.get("transcript") or "").strip()
    if data.get("use_sample"):
        transcript = g.SAMPLE_TRANSCRIPT
    settings = g.Settings(tone=tone, length=length_key, variants=variants,
                          news_lookback_hours=lookback,
                          images=bool(data.get("images", True)))
    # Extra attrs consumed by _run_job / generator.generate_post
    settings.max_tokens = min(8000, max(700, int(word_count * 2.2) + 300))
    settings.max_chars = max(2900, word_count * 8)

    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {"status": "running", "progress": "Starting...",
                        "result": None, "error": None}
    threading.Thread(target=_run_job, args=(job_id, settings, transcript),
                     daemon=True).start()
    return jsonify({"job_id": job_id})


@app.get("/api/status/<job_id>")
def api_status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return jsonify({"error": "unknown job"}), 404
        return jsonify(job)


@app.get("/api/health")
def api_health():
    return jsonify({"ok": True, "llm": g.llm_available(),
                    "pexels": bool(g.PEXELS_API_KEY),
                    "tavily": bool(g.TAVILY_API_KEY)})


# --------------------------- Static pages ---------------------------------

@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/app")
def generator_page():
    return send_from_directory(BASE_DIR, "generator.html")


@app.get("/styles.css")
def styles():
    return send_from_directory(BASE_DIR, "styles.css")


@app.get("/app.js")
def appjs():
    return send_from_directory(BASE_DIR, "app.js")


@app.get("/images/<path:filename>")
def images(filename):
    return send_from_directory(os.path.join(BASE_DIR, g.IMAGE_DIR), filename)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    print(f"LinkedIn Post Generator running at http://localhost:{port}")
    if not g.llm_available():
        print("[info] No ANTHROPIC_API_KEY - posts will use the template fallback.")
    app.run(host="0.0.0.0", port=port, debug=False)
