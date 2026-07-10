#!/usr/bin/env python3
"""LinkedIn Post Generator - single file. Live AI news + a transcript -> posts.
Now also attaches a license-safe stock photo to each post (Openverse free, no key;
or Pexels if PEXELS_API_KEY is set). Set ANTHROPIC_API_KEY for AI-written posts."""
from __future__ import annotations
import argparse, glob, json, os, re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
try:
    import requests
except Exception:
    requests = None
try:
    import feedparser
except Exception:
    feedparser = None
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-5")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "output")
IMAGE_DIR = os.path.join(OUTPUT_DIR, "images")
CONTENT, VOICE = "content", "voice"
@dataclass
class Settings:
    tone: str = "insightful"
    length: str = "medium"
    variants: int = 3
    news_lookback_hours: int = 48
    max_news_items: int = 6
    images: bool = True
SAMPLE_TRANSCRIPT = """\
Sarah: Where are we with the onboarding rewrite?
Miguel: Getting there. Everyone wants to bolt AI onto the flow, but I don't think
we've earned it yet. We haven't nailed the boring basics - empty states, error
messages - and now there's pressure to drop a chatbot in the corner. I'm allergic
to that. AI should remove steps, not add a shiny thing you have to talk to.
Sarah: Leadership sees competitors shipping copilots everywhere.
Miguel: Most of them are demos, not products. That's my whole thing lately - the
gap between a demo that wows in a meeting and something that survives contact with
real users on a stressed Tuesday morning. I'd rather ship one AI feature that
quietly saves thirty seconds than five that feel like managing a junior intern.
The teams that win aren't the ones with the fanciest model - they're obsessive
about where AI reduces friction versus where it's just theater. And we measure it.
If it doesn't move activation, we rip it out. No sacred AI cows.
"""
@dataclass
class Signal:
    kind: str; lane: str; text: str
    title: str = ""; url: str = ""; date: str | None = None
    weight: float = 1.0; meta: dict = field(default_factory=dict)
    def short(self, n: int = 160) -> str:
        t = self.text.strip().replace("\n", " ")
        return t if len(t) <= n else t[:n-1] + "..."
def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()
_client = None
def llm_available() -> bool:
    return bool(ANTHROPIC_API_KEY)
def llm_complete(prompt, system="", max_tokens=1024, temperature=0.7):
    global _client
    if not llm_available():
        raise RuntimeError("No ANTHROPIC_API_KEY")
    if _client is None:
        from anthropic import Anthropic
        _client = Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = _client.messages.create(model=MODEL, max_tokens=max_tokens,
        temperature=temperature, system=system or "You are a concise assistant.",
        messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in msg.content if getattr(b,"type","")=="text").strip()
def _strip_html(t): return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)).strip()
def fetch_ai_news(s: Settings):
    if TAVILY_API_KEY and requests is not None:
        try:
            r = requests.post("https://api.tavily.com/search", json={
                "api_key": TAVILY_API_KEY,
                "query": "latest artificial intelligence news this week",
                "topic": "news", "days": max(1, s.news_lookback_hours//24),
                "max_results": s.max_news_items}, timeout=20)
            r.raise_for_status()
            out = [Signal("ai_news", CONTENT, x.get("content","") or x.get("title",""),
                x.get("title",""), x.get("url",""), x.get("published_date") or _today(),
                float(x.get("score",0.5))+0.5) for x in r.json().get("results",[])]
            if out: return out
        except Exception as e:
            print(f"  [info] Tavily failed ({e}); using RSS.")
    return _fetch_rss(s)
RSS_FEEDS = ["https://www.technologyreview.com/topic/artificial-intelligence/feed",
    "https://venturebeat.com/category/ai/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"]
def _fetch_rss(s: Settings):
    if feedparser is None:
        print("  [warn] feedparser not installed; no news."); return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=s.news_lookback_hours)
    items = []
    for url in RSS_FEEDS:
        try: feed = feedparser.parse(url)
        except Exception: continue
        for e in feed.entries:
            tm = getattr(e,"published_parsed",None) or getattr(e,"updated_parsed",None)
            dt = datetime(*tm[:6], tzinfo=timezone.utc) if tm else None
            if dt and dt < cutoff: continue
            items.append((dt or datetime.now(timezone.utc), Signal("ai_news", CONTENT,
                _strip_html(getattr(e,"summary","")) or getattr(e,"title",""),
                getattr(e,"title",""), getattr(e,"link",""),
                (dt or datetime.now(timezone.utc)).date().isoformat(), 1.0)))
    items.sort(key=lambda t: t[0], reverse=True)
    return [sig for _, sig in items[:s.max_news_items]]
def fetch_trending(s: Settings):
    if requests is None: return []
    try:
        r = requests.get("https://hn.algolia.com/api/v1/search_by_date", params={
            "query": "AI OR LLM OR OpenAI OR Anthropic", "tags": "story",
            "numericFilters": "points>50", "hitsPerPage": 6}, timeout=15)
        r.raise_for_status()
    except Exception: return []
    out = []
    for h in r.json().get("hits", []):
        title = h.get("title") or ""
        if not title: continue
        out.append(Signal("trending", CONTENT,
            f"{title} - {h.get('points',0)} points, {h.get('num_comments',0)} comments on HN.",
            title, h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
            (h.get("created_at") or "")[:10] or _today(), 0.6))
    return out
def fetch_arxiv(s: Settings):
    if requests is None or feedparser is None: return []
    try:
        r = requests.get("http://export.arxiv.org/api/query", params={
            "search_query": "cat:cs.AI OR cat:cs.LG OR cat:cs.CL",
            "sortBy": "submittedDate", "sortOrder": "descending",
            "max_results": 4}, timeout=20)
        r.raise_for_status(); feed = feedparser.parse(r.text)
    except Exception: return []
    return [Signal("arxiv", CONTENT,
        (getattr(e,"summary","") or "").strip().replace("\n"," ")[:500],
        getattr(e,"title","").strip(), getattr(e,"link",""),
        (getattr(e,"published","") or "")[:10] or _today(), 0.8) for e in feed.entries]
def fetch_transcript(text):
    text = (text or "").strip()
    return [Signal("transcript", VOICE, text, "conversation", "", _today(), 2.0)] if text else []
def fetch_local_voice(directory, kind):
    out = []
    for p in sorted(glob.glob(os.path.join(directory, "*.txt"))):
        try:
            with open(p, "r", encoding="utf-8") as f: t = f.read().strip()
        except OSError: continue
        if t: out.append(Signal(kind, VOICE, t, os.path.basename(p), "", _today(), 1.5))
    return out
def gather_signals(s: Settings, transcript_text):
    print("-> Gathering sources...")
    content = []
    for fn in (fetch_ai_news, fetch_trending, fetch_arxiv):
        try: got = fn(s)
        except Exception as e: print(f"  [warn] {fn.__name__} failed: {e}"); got = []
        if got: print(f"  . {fn.__name__}: {len(got)} signal(s)")
        content.extend(got)
    voice = fetch_transcript(transcript_text)
    voice += fetch_local_voice("data/past_posts", "past_post")
    voice += fetch_local_voice("data/notes", "notes")
    return content, voice
@dataclass
class VoiceProfile:
    summary: str = ""; themes: list = field(default_factory=list)
    opinions: list = field(default_factory=list); style_notes: list = field(default_factory=list)
    def to_prompt_block(self):
        def b(x): return "\n".join(f"- {i}" for i in x) if x else "- (none)"
        return (f"WHO THEY ARE / HOW THEY WRITE:\n{self.summary or '(unknown)'}\n\n"
            f"THEMES:\n{b(self.themes)}\n\nOPINIONS:\n{b(self.opinions)}\n\n"
            f"STYLE NOTES (mimic these):\n{b(self.style_notes)}")
def build_voice_profile(voice_signals):
    if not voice_signals: return VoiceProfile(summary="No voice inputs provided.")
    if llm_available():
        try:
            corpus = "\n\n".join(f"### {s.kind}\n{s.text[:6000]}" for s in voice_signals)
            raw = llm_complete("Read the material and return ONLY a JSON object with keys "
                "`summary` (2-3 sentences), `themes` (array), `opinions` (array of stances "
                "they actually expressed), `style_notes` (array about sentence length, tone, "
                "hooks, hashtag habits).\n\n" + corpus,
                system="You profile how a person thinks and writes so someone could draft in "
                "their voice. Be specific. Don't invent beliefs.", max_tokens=1200, temperature=0.3)
            d = _loads_lenient(raw)
            return VoiceProfile(str(d.get("summary","")).strip(),
                [str(x) for x in d.get("themes",[])][:8],
                [str(x) for x in d.get("opinions",[])][:8],
                [str(x) for x in d.get("style_notes",[])][:8])
        except Exception as e:
            print(f"  [warn] LLM voice extraction failed ({e}); using heuristic.")
    words = " ".join(s.text for s in voice_signals).split()
    return VoiceProfile(summary=f"Heuristic profile (no LLM). Own words: \"{' '.join(words[:50])}...\"",
        style_notes=["Mirror the vocabulary and rhythm of the quoted sample."])
def _loads_lenient(raw):
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw); raw = re.sub(r"\n?```$", "", raw).strip()
    try: return json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try: return json.loads(m.group(0))
            except Exception: pass
    return {}
@dataclass
class Topic:
    primary: Signal; supporting: list = field(default_factory=list)
    def to_prompt_block(self):
        blk = (f"HEADLINE: {self.primary.title or self.primary.short(80)}\n"
            f"WHAT HAPPENED: {self.primary.short(400)}\n"
            f"SOURCE: {self.primary.url or '(no link)'} | DATE: {self.primary.date or 'recent'}")
        if self.supporting:
            blk += "\n\nRELATED DISCUSSION:\n" + "\n".join(f"- ({s.kind}) {s.short(140)}" for s in self.supporting)
        return blk
def select_topics(content, n):
    primaries = [s for s in content if s.kind in ("ai_news","arxiv")] or list(content)
    supports = [s for s in content if s.kind == "trending"]
    primaries = sorted(primaries, key=lambda s: s.weight, reverse=True)
    topics = []
    for p in primaries[:max(1, n)]:
        key = _keywords(p.title + " " + p.text)
        matched = [s for s in supports if key & _keywords(s.title + " " + s.text)]
        matched = sorted(matched, key=lambda s: len(key & _keywords(s.title+" "+s.text)), reverse=True)[:2]
        topics.append(Topic(p, matched))
    return topics
def _keywords(text):
    stop = {"the","a","an","and","or","to","of","in","on","with","is","are","for",
        "this","that","it","as","at","by","new","ai","how"}
    return {w for w in re.findall(r"[a-zA-Z][a-zA-Z0-9\-]{2,}", text.lower()) if w not in stop}
# ---------------------------------------------------------------------------
#  IMAGE SOURCING (license-safe: Openverse free / Pexels optional)
# ---------------------------------------------------------------------------
def build_image_query(topic, post_text=""):
    seed = topic.primary.title or topic.primary.short(80)
    ctx = f"HEADLINE: {seed}"
    if post_text:
        ctx += f"\n\nTHE POST THE IMAGE WILL ACCOMPANY:\n{post_text[:500]}"
    if llm_available():
        try:
            raw = llm_complete(
                "Give a SHORT stock-photo search query (3-6 words, generic and "
                "conceptual - NO named people, companies, or logos, so it's "
                "license-safe) that visually fits the post below, plus one line "
                'of alt text. Return ONLY JSON {"query":..., "alt":...}.\n\n' + ctx,
                system="You choose safe, relevant stock-photo search terms.",
                max_tokens=200, temperature=0.3)
            d = _loads_lenient(raw)
            q, alt = str(d.get("query","")).strip(), str(d.get("alt","")).strip()
            if q: return q, (alt or seed)
        except Exception as e:
            print(f"  [warn] image query via LLM failed ({e}); using keywords.")
    return (" ".join(list(_keywords(seed))[:4]) or "technology"), seed
def _search_pexels(query, count=1):
    if requests is None or not PEXELS_API_KEY: return []
    try:
        r = requests.get("https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={"query": query, "per_page": count, "orientation": "landscape"}, timeout=20)
        r.raise_for_status()
        return [{"url": p["src"]["large"], "source": "Pexels",
            "license": "Pexels License (free commercial use; attribution optional)",
            "attribution": f"Photo by {p.get('photographer','')} on Pexels",
            "page": p.get("url", ""), "alt": p.get("alt", "")}
            for p in r.json().get("photos", [])]
    except Exception as e:
        print(f"  [warn] Pexels search failed ({e}).")
        return []
def _search_openverse(query, count=1):
    if requests is None: return []
    try:
        r = requests.get("https://api.openverse.org/v1/images/",
            params={"q": query, "page_size": count, "license_type": "commercial"},
            headers={"User-Agent": "linkedin-post-generator"}, timeout=20)
        r.raise_for_status()
        out = []
        for it in r.json().get("results", []):
            lic = f"{it.get('license','')} {it.get('license_version','')}".strip().upper()
            out.append({"url": it.get("url", ""), "source": it.get("source", "Openverse"),
                "license": f"CC {lic}".strip() + " (verify credit requirement)",
                "attribution": it.get("attribution") or
                    f"{it.get('creator','Unknown')} via {it.get('source','Openverse')}",
                "page": it.get("foreign_landing_url", ""),
                "alt": it.get("title", "")})
        return out
    except Exception as e:
        print(f"  [warn] Openverse search failed ({e}).")
        return []
def _pick_best_image(candidates, context):
    """Ask the LLM which candidate's description best matches the post."""
    if len(candidates) < 2 or not context or not llm_available():
        return candidates[0]
    try:
        lines = "\n".join(f"{i}. {c.get('alt') or c.get('page') or c.get('url')}"
                          for i, c in enumerate(candidates))
        raw = llm_complete(
            f"THE POST:\n{context[:400]}\n\nCANDIDATE IMAGES (descriptions):\n"
            f"{lines}\n\nReply with ONLY the number of the image that best "
            "matches the post's topic and tone.",
            system="You pick the most relevant stock photo. Output one number.",
            max_tokens=10, temperature=0.0)
        m = re.search(r"\d+", raw)
        i = int(m.group(0)) if m else 0
        return candidates[i] if 0 <= i < len(candidates) else candidates[0]
    except Exception as e:
        print(f"  [warn] image ranking failed ({e}); using first result.")
        return candidates[0]
def find_image(query, index, context=""):
    """Return dict {local_path, source, license, attribution, page, url, query} or None."""
    candidates = _search_pexels(query, 6) or _search_openverse(query, 6)
    if not candidates: return None
    info = _pick_best_image(candidates, context)
    info["query"] = query
    info["local_path"] = ""
    if requests is not None and info.get("url"):
        try:
            os.makedirs(IMAGE_DIR, exist_ok=True)
            resp = requests.get(info["url"], timeout=30,
                headers={"User-Agent": "linkedin-post-generator"})
            resp.raise_for_status()
            path = os.path.join(IMAGE_DIR, f"variant-{index}.jpg")
            with open(path, "wb") as f: f.write(resp.content)
            info["local_path"] = path
        except Exception as e:
            print(f"  [warn] could not download image ({e}).")
    return info
# ---------------------------------------------------------------------------
#  GENERATION
# ---------------------------------------------------------------------------
LENGTH_GUIDE = {"short":"about 60-100 words (3-5 short lines).","medium":"about 130-180 words.","long":"about 200-260 words."}
TONE_GUIDE = {"insightful":"thoughtful and analytical; earn respect with a real insight.",
    "casual":"conversational and warm, like a smart friend.",
    "bold":"opinionated and punchy; take a clear stance.",
    "technical":"precise and credible for an expert audience."}
DEFAULT_TAGS = ["ArtificialIntelligence","AI","MachineLearning","FutureOfWork"]
def generate_post(topic, voice, s: Settings):
    if llm_available():
        try:
            prompt = f"""Write ONE LinkedIn post.
The post is by a specific person reacting to a current AI development, seen through
THEIR OWN ideas and voice. Connect the news to what they already think - do not
just report it.
=== THE PERSON (write AS them) ===
{voice.to_prompt_block()}
=== THE NEWS THEY'RE REACTING TO ===
{topic.to_prompt_block()}
=== RULES ===
- Open with a scroll-stopping first line. No "I'm excited to share".
- {TONE_GUIDE.get(s.tone, TONE_GUIDE['insightful'])}
- Length: {LENGTH_GUIDE.get(s.length, LENGTH_GUIDE['medium'])}
- Tie the news to one of their themes or opinions explicitly.
- Flawless grammar, spelling, and punctuation. Complete sentences only -
  no fragments unless used deliberately once for punch.
- Structure: paragraphs of 1-3 sentences each, with a blank line between
  EVERY paragraph. Never write a wall of text. One idea per paragraph, in
  logical order: hook, context, insight, takeaway, question.
- Plain text only - NO markdown, NO bullet characters, NO emoji spam.
- End with a genuine question. Finish with 3-5 hashtags on the last line.
- Before finishing, re-read the post and fix any grammatical or flow issues.
- Output ONLY the post text."""
            return llm_complete(prompt, system="You are a ghostwriter who writes authentic, "
                "human LinkedIn posts. You never sound like an AI.",
                max_tokens=getattr(s, "max_tokens", 700), temperature=0.8)
        except Exception as e:
            print(f"  [warn] LLM generation failed ({e}); using template.")
    theme = f"This lands on something I keep coming back to: {voice.themes[0]}." if voice.themes \
        else "This is exactly the kind of shift worth watching."
    return (f"{topic.primary.title or 'Worth noting in AI today.'}\n\n{topic.primary.short(220)}\n\n"
        f"{theme}\n\nWhat's your read on this?\n\n" + " ".join(f"#{t}" for t in DEFAULT_TAGS))
def format_post(text, max_chars=2900):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text); text = re.sub(r"\n?```$", "", text).strip()
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*]\s+", "* ", text, flags=re.MULTILINE)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    text = text.replace("`", "")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > max_chars: text = text[:max_chars].rsplit("\n", 1)[0].rstrip()
    return text
def save_run(drafts, s: Settings):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(OUTPUT_DIR, f"posts-{stamp}.md")
    lines = [f"# LinkedIn drafts - {stamp}", f"_Tone: {s.tone} | Length: {s.length}_", ""]
    for i, (text, topic, img) in enumerate(drafts, 1):
        lines += [f"## Variant {i}", "", "```", text, "```", "",
            f"**Based on:** {topic.primary.title} ({topic.primary.url or 'no link'}) - {topic.primary.date}"]
        if img:
            lines += ["", f"**Suggested image:** {img.get('local_path') or img.get('url')}",
                f"- Source: {img.get('source')}  |  License: {img.get('license')}",
                f"- Credit: {img.get('attribution')}",
                f"- Image page: {img.get('page') or '(n/a)'}",
                f"- Search used: \"{img.get('query')}\""]
        else:
            lines += ["", "**Suggested image:** (none found - try rerunning or add a PEXELS_API_KEY)"]
        lines.append("")
    with open(path, "w", encoding="utf-8") as f: f.write("\n".join(lines))
    return path
def run(s: Settings, transcript_text):
    content, voice_signals = gather_signals(s, transcript_text)
    if not content: raise SystemExit("No news signals - check internet / install feedparser.")
    print("-> Building voice profile...")
    voice = build_voice_profile(voice_signals)
    print("-> Selecting topics...")
    topics = select_topics(content, s.variants)
    print(f"-> Generating {len(topics)} variant(s)...")
    drafts = []
    for i, topic in enumerate(topics, 1):
        text = format_post(generate_post(topic, voice, s))
        img = None
        if s.images:
            try:
                q, _alt = build_image_query(topic)
                img = find_image(q, i)
            except Exception as e:
                print(f"  [warn] image step failed ({e}).")
        drafts.append((text, topic, img))
        src = f" + image ({img['source']})" if img else ""
        print(f"  . variant {i} on: {topic.primary.title or topic.primary.short(50)}{src}")
    path = save_run(drafts, s)
    print("\n" + "="*70)
    for i, (text, _topic, img) in enumerate(drafts, 1):
        print(f"\n----- VARIANT {i} " + "-"*52 + f"\n{text}")
        if img:
            print(f"\n[image] {img.get('local_path') or img.get('url')}")
            print(f"[image] {img.get('source')} | {img.get('license')} | {img.get('attribution')}")
    print("\n" + "="*70)
    print(f"\nSaved to: {path}\nImages (if any) in: {IMAGE_DIR}")
    print("Copy the post you like into LinkedIn and attach its saved image.")
def main():
    ap = argparse.ArgumentParser(description="LinkedIn post generator (single file)")
    ap.add_argument("--transcript", help="Path to a transcript .txt (else built-in sample)")
    ap.add_argument("--tone", default="insightful", choices=["insightful","casual","bold","technical"])
    ap.add_argument("--length", default="medium", choices=["short","medium","long"])
    ap.add_argument("--variants", type=int, default=3)
    ap.add_argument("--lookback", type=int, default=48)
    ap.add_argument("--no-images", action="store_true", help="Skip attaching stock photos")
    args = ap.parse_args()
    if args.transcript and os.path.exists(args.transcript):
        with open(args.transcript, "r", encoding="utf-8") as f: transcript_text = f.read()
    else:
        if args.transcript: print(f"  [info] '{args.transcript}' not found; using built-in sample.")
        transcript_text = SAMPLE_TRANSCRIPT
    if not llm_available():
        print("  [info] No ANTHROPIC_API_KEY set - fallback mode (free news + template).\n")
    run(Settings(tone=args.tone, length=args.length, variants=max(1, min(5, args.variants)),
        news_lookback_hours=args.lookback, images=not args.no_images), transcript_text)
if __name__ == "__main__":
    main()
