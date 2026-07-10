#!/usr/bin/env python3
"""Web app for the LinkedIn Post Generator.

Run:  pip install flask requests feedparser anthropic
      python app.py
Then open http://localhost:5000

Env (optional): ANTHROPIC_API_KEY (AI-written posts), TAVILY_API_KEY,
PEXELS_API_KEY, LLM_MODEL, PORT.
"""
import os
import secrets
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

import requests
from flask import (Flask, jsonify, redirect, request, send_from_directory,
                   session)


def _load_dotenv(path=".env"):
    """Load KEY=VALUE lines from a local .env file (no dependency needed).
    Real environment variables always win over .env values."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and v and k not in os.environ:
                    os.environ[k] = v
    except OSError:
        pass


_load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

import generator as g

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=None)
# Needed for login sessions. Set SECRET_KEY in production so logins
# survive server restarts.
app.secret_key = os.getenv("SECRET_KEY") or secrets.token_hex(32)
# Keep users signed in for ~60 days (matches LinkedIn token lifetime)
from datetime import timedelta
app.permanent_session_lifetime = timedelta(days=60)

# --- Drafts library (SQLite) ----------------------------------------------
DB_PATH = os.path.join(BASE_DIR, "postpilot.db")


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    with _db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS posts (
            id TEXT PRIMARY KEY,
            created TEXT,
            text TEXT,
            source_title TEXT, source_url TEXT, source_date TEXT,
            image_src TEXT, image_source TEXT, image_license TEXT,
            image_attribution TEXT, image_page TEXT,
            status TEXT DEFAULT 'saved',
            posted_at TEXT)""")


_init_db()


def _save_draft(draft):
    img = draft.get("image") or {}
    with _db() as c:
        c.execute(
            "INSERT INTO posts (id, created, text, source_title, source_url, "
            "source_date, image_src, image_source, image_license, "
            "image_attribution, image_page, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,'saved')",
            (draft["id"], draft["created"], draft["text"],
             draft["source"].get("title", ""), draft["source"].get("url", ""),
             draft["source"].get("date", ""),
             img.get("src", ""), img.get("source", ""),
             img.get("license", ""), img.get("attribution", ""),
             img.get("page", "")))


def _row_to_draft(r):
    d = {"id": r["id"], "created": r["created"], "text": r["text"],
         "status": r["status"], "posted_at": r["posted_at"],
         "source": {"title": r["source_title"], "url": r["source_url"],
                    "date": r["source_date"]},
         "image": None}
    if r["image_src"]:
        d["image"] = {"src": r["image_src"], "source": r["image_source"],
                      "license": r["image_license"],
                      "attribution": r["image_attribution"],
                      "page": r["image_page"]}
    return d

# --- LinkedIn OAuth config (set these env vars to enable posting) ---
LI_CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID", "")
LI_CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET", "")
BASE_URL = os.getenv("BASE_URL", "http://localhost:5000").rstrip("/")
LI_REDIRECT_URI = BASE_URL + "/auth/callback"
app.config.update(SESSION_COOKIE_SECURE=BASE_URL.startswith("https"),
                  SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SAMESITE="Lax")

# In-memory job store: jobs run in a thread so the browser can poll progress
# instead of holding one long request open.
JOBS = {}  # id -> {"status", "progress", "result", "error"}
JOBS_LOCK = threading.Lock()


def _job_update(job_id, **kw):
    with JOBS_LOCK:
        JOBS[job_id].update(kw)


def _serialize_draft(text, topic, img, draft_id):
    d = {
        "id": draft_id,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
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


def _rank_topics_for_voice(content, voice, transcript_text, n):
    """Boost the weight of news signals most relevant to the user's
    themes/opinions so select_topics() favors them over generic top headlines."""
    primaries = [s for s in content if s.kind in ("ai_news", "arxiv")] or list(content)
    if len(primaries) <= n:
        return
    interests = "; ".join((voice.themes or []) + (voice.opinions or []))
    if not interests.strip():
        interests = (transcript_text or "")[:600]
    if not interests.strip():
        return

    if g.llm_available():
        try:
            lines = "\n".join(f"{i}. {s.title or s.short(80)}"
                              for i, s in enumerate(primaries))
            raw = g.llm_complete(
                f"A person cares about these topics and opinions:\n{interests}\n\n"
                f"Candidate news headlines:\n{lines}\n\n"
                f"Return ONLY a JSON array with the indices of the {n} headlines "
                "most relevant to what this person cares about, most relevant "
                "first. Example: [4, 0, 7]",
                system="You rank news headlines by relevance to a person's "
                       "interests. Output only the JSON array.",
                max_tokens=100, temperature=0.1)
            import json as _json
            import re as _re
            cleaned = _re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", raw.strip())
            try:
                idx = [int(x) for x in _json.loads(cleaned)]
            except Exception:
                idx = [int(x) for x in _re.findall(r"\d+", cleaned)]
            for rank, i in enumerate(idx[:n]):
                if 0 <= i < len(primaries):
                    primaries[i].weight += 100 - rank
            return
        except Exception as e:
            print(f"  [warn] LLM topic ranking failed ({e}); using keywords.")

    # Fallback: keyword overlap between interests and each headline
    key = g._keywords(interests + " " + (transcript_text or ""))
    for s in primaries:
        s.weight += 1.5 * len(key & g._keywords(s.title + " " + s.text))


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

        _job_update(job_id, progress="Matching news to your topics...")
        try:
            _rank_topics_for_voice(content, voice, transcript_text,
                                   settings.variants)
        except Exception as e:
            print(f"  [warn] topic ranking failed ({e}).")
        topics = g.select_topics(content, settings.variants)

        drafts = []
        for i, topic in enumerate(topics, 1):
            _job_update(job_id, progress=f"Writing variant {i} of {len(topics)}...")
            text = g.format_post(g.generate_post(topic, voice, settings),
                                 max_chars=getattr(settings, "max_chars", 2900))
            draft_id = uuid.uuid4().hex[:12]
            img = None
            if settings.images:
                try:
                    _job_update(job_id, progress=f"Finding image for variant {i}...")
                    q, _alt = g.build_image_query(topic, post_text=text)
                    img = g.find_image(q, i, context=text)
                    # Give the image a unique name so library drafts keep
                    # their picture after later runs overwrite variant-N.jpg
                    if img and img.get("local_path"):
                        unique = os.path.join(BASE_DIR, g.IMAGE_DIR,
                                              f"{draft_id}.jpg")
                        shutil.copyfile(img["local_path"], unique)
                        img["local_path"] = unique
                except Exception as e:
                    print(f"  [warn] image step failed ({e}).")
            drafts.append(_serialize_draft(text, topic, img, draft_id))

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

    # Target word range: 0-3000 (both 0 = as short as possible)
    def _wc(key, default):
        try:
            return max(0, min(3000, int(data.get(key, default))))
        except (TypeError, ValueError):
            return default
    word_min, word_max = _wc("word_min", 100), _wc("word_max", 200)
    if word_min > word_max:
        word_min, word_max = word_max, word_min
    length_key = f"custom-{word_min}-{word_max}"
    if word_max == 0:
        g.LENGTH_GUIDE[length_key] = "as short as possible - a single punchy line."
    elif word_min == word_max:
        g.LENGTH_GUIDE[length_key] = (f"about {word_max} words. Aim close to "
                                      f"this word count.")
    else:
        g.LENGTH_GUIDE[length_key] = (f"between {word_min} and {word_max} words. "
                                      f"Stay inside this range.")

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
                          max_news_items=12,
                          images=bool(data.get("images", True)))
    # Extra attrs consumed by _run_job / generator.generate_post
    settings.max_tokens = min(8000, max(700, int(word_max * 2.2) + 300))
    settings.max_chars = max(2900, word_max * 8)

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


@app.post("/api/library/save")
def api_library_save():
    d = request.get_json(silent=True) or {}
    if not d.get("id") or not (d.get("text") or "").strip():
        return jsonify({"error": "Invalid draft."}), 400
    d.setdefault("source", {})
    d.setdefault("created",
                 datetime.now(timezone.utc).isoformat(timespec="seconds"))
    try:
        _save_draft(d)
    except sqlite3.IntegrityError:
        return jsonify({"ok": True, "already_saved": True})
    return jsonify({"ok": True})


@app.get("/api/library")
def api_library():
    with _db() as c:
        rows = c.execute("SELECT * FROM posts ORDER BY created DESC").fetchall()
    return jsonify({"drafts": [_row_to_draft(r) for r in rows]})


@app.post("/api/library/<pid>/delete")
def api_library_delete(pid):
    with _db() as c:
        row = c.execute("SELECT image_src FROM posts WHERE id=?", (pid,)).fetchone()
        c.execute("DELETE FROM posts WHERE id=?", (pid,))
    # Clean up the image file if it was a locally stored one
    if row and (row["image_src"] or "").startswith("/images/"):
        p = os.path.join(BASE_DIR, g.IMAGE_DIR, os.path.basename(row["image_src"]))
        try:
            os.remove(p)
        except OSError:
            pass
    return jsonify({"ok": True})


@app.get("/api/health")
def api_health():
    return jsonify({"ok": True, "llm": g.llm_available(),
                    "pexels": bool(g.PEXELS_API_KEY),
                    "tavily": bool(g.TAVILY_API_KEY)})


# --------------------------- LinkedIn ------------------------------------

@app.get("/auth/linkedin")
def auth_linkedin():
    if not LI_CLIENT_ID:
        return ("LinkedIn is not configured on this server "
                "(missing LINKEDIN_CLIENT_ID).", 503)
    state = secrets.token_urlsafe(16)
    session["li_state"] = state
    from urllib.parse import urlencode
    params = urlencode({
        "response_type": "code",
        "client_id": LI_CLIENT_ID,
        "redirect_uri": LI_REDIRECT_URI,
        "state": state,
        "scope": "openid profile w_member_social",
    })
    return redirect("https://www.linkedin.com/oauth/v2/authorization?" + params)


@app.get("/auth/callback")
def auth_callback():
    err = request.args.get("error_description") or request.args.get("error")
    if err:
        return redirect("/app?li_error=" + err[:200])
    if request.args.get("state") != session.pop("li_state", None):
        return redirect("/app?li_error=state_mismatch")
    code = request.args.get("code", "")
    try:
        r = requests.post("https://www.linkedin.com/oauth/v2/accessToken",
                          data={"grant_type": "authorization_code",
                                "code": code,
                                "redirect_uri": LI_REDIRECT_URI,
                                "client_id": LI_CLIENT_ID,
                                "client_secret": LI_CLIENT_SECRET},
                          timeout=20)
        r.raise_for_status()
        token = r.json()["access_token"]
        u = requests.get("https://api.linkedin.com/v2/userinfo",
                         headers={"Authorization": f"Bearer {token}"},
                         timeout=20)
        u.raise_for_status()
        info = u.json()
        session.permanent = True
        session["li_token"] = token
        session["li_sub"] = info.get("sub", "")
        session["li_name"] = info.get("name", "LinkedIn user")
        return redirect("/app")
    except Exception as e:
        return redirect("/app?li_error=" + str(e)[:200])


@app.get("/api/linkedin/status")
def linkedin_status():
    return jsonify({
        "configured": bool(LI_CLIENT_ID),
        "connected": bool(session.get("li_token")),
        "name": session.get("li_name", ""),
    })


@app.post("/api/linkedin/logout")
def linkedin_logout():
    for k in ("li_token", "li_sub", "li_name"):
        session.pop(k, None)
    return jsonify({"ok": True})


def _li_upload_image(token, author_urn, local_path):
    """Register + upload an image asset. Returns the asset URN."""
    reg = requests.post(
        "https://api.linkedin.com/v2/assets?action=registerUpload",
        headers={"Authorization": f"Bearer {token}",
                 "X-Restli-Protocol-Version": "2.0.0"},
        json={"registerUploadRequest": {
            "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
            "owner": author_urn,
            "serviceRelationships": [{
                "relationshipType": "OWNER",
                "identifier": "urn:li:userGeneratedContent"}]}},
        timeout=20)
    reg.raise_for_status()
    v = reg.json()["value"]
    upload_url = v["uploadMechanism"][
        "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"]["uploadUrl"]
    asset = v["asset"]
    with open(local_path, "rb") as f:
        up = requests.put(upload_url, data=f.read(),
                          headers={"Authorization": f"Bearer {token}"},
                          timeout=60)
    up.raise_for_status()
    return asset


@app.post("/api/linkedin/post")
def linkedin_post():
    token = session.get("li_token")
    sub = session.get("li_sub")
    if not token or not sub:
        return jsonify({"error": "Not connected to LinkedIn."}), 401
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "Post text is empty."}), 400
    author = f"urn:li:person:{sub}"

    media = []
    category = "NONE"
    image_src = data.get("image_src") or ""
    if image_src.startswith("/images/"):
        local = os.path.join(BASE_DIR, g.IMAGE_DIR,
                             os.path.basename(image_src))
        if os.path.exists(local):
            try:
                asset = _li_upload_image(token, author, local)
                media = [{"status": "READY", "media": asset}]
                category = "IMAGE"
            except Exception as e:
                print(f"  [warn] LinkedIn image upload failed ({e}); "
                      f"posting text only.")

    body = {
        "author": author,
        "lifecycleState": "PUBLISHED",
        "specificContent": {"com.linkedin.ugc.ShareContent": {
            "shareCommentary": {"text": text},
            "shareMediaCategory": category,
            **({"media": media} if media else {}),
        }},
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }
    try:
        r = requests.post("https://api.linkedin.com/v2/ugcPosts",
                          headers={"Authorization": f"Bearer {token}",
                                   "X-Restli-Protocol-Version": "2.0.0"},
                          json=body, timeout=30)
        if r.status_code == 401:
            for k in ("li_token", "li_sub", "li_name"):
                session.pop(k, None)
            return jsonify({"error": "LinkedIn session expired - "
                                     "please reconnect."}), 401
        r.raise_for_status()
        post_id = r.headers.get("x-restli-id") or r.json().get("id", "")
        # Mark the library draft as posted
        draft_id = data.get("draft_id")
        if draft_id:
            try:
                with _db() as c:
                    c.execute("UPDATE posts SET status='posted', posted_at=? "
                              "WHERE id=?",
                              (datetime.now(timezone.utc).isoformat(
                                  timespec="seconds"), draft_id))
            except Exception as e:
                print(f"  [warn] could not mark draft posted ({e}).")
        return jsonify({"ok": True, "post_id": post_id,
                        "with_image": category == "IMAGE"})
    except requests.HTTPError as e:
        detail = ""
        try:
            detail = e.response.json().get("message", "")[:300]
        except Exception:
            pass
        return jsonify({"error": f"LinkedIn rejected the post. {detail}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)[:300]}), 502


# --------------------------- Static pages ---------------------------------

@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/app")
def generator_page():
    return send_from_directory(BASE_DIR, "generator.html")


@app.get("/library")
def library_page():
    return send_from_directory(BASE_DIR, "library.html")


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
