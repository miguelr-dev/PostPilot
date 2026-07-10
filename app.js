/* PostPilot generator page logic */
(() => {
  const $ = (id) => document.getElementById(id);

  // Sample transcripts by topic - opinionated notes the AI can learn a voice from
  const SAMPLES = {
    ai: `Everyone wants to bolt AI onto their product, but most of it is theater. I'd rather ship one AI feature that quietly saves users thirty seconds than five that feel like managing a junior intern. The gap between a demo that wows in a meeting and something that survives real users on a stressed Tuesday morning is enormous. If it doesn't move activation, rip it out. No sacred AI cows.`,
    economics: `Everyone's watching the Fed like it's the only thing that matters, but the real story is in small business credit. When local businesses can't borrow affordably, hiring freezes long before the headlines catch up. I think we pay too much attention to the stock market and too little to Main Street indicators. GDP numbers hide more than they reveal about how regular people are actually doing.`,
    startups: `Most startups don't die from competition, they die from building something nobody asked for. Talk to twenty customers before you write a line of code. I'm also convinced raising too much money too early kills more companies than raising too little - constraints force focus. Revenue is the only validation that matters. Everything else is applause.`,
    marketing: `Brand isn't your logo, it's what people say about you when you're not in the room. Most companies pump out content nobody reads instead of making one thing people actually share. I believe boring industries are the biggest branding opportunity - if everyone in your category sounds the same, sounding human is a superpower. Metrics without a story are just noise.`,
    careers: `Job descriptions asking for five years of experience in a three-year-old technology tell you everything about broken hiring. I believe skills beat credentials, and portfolios beat resumes. The best career moves I've seen came from people who built things in public instead of quietly applying to hundreds of roles. Networking isn't schmoozing - it's being useful to people before you need them.`,
    leadership: `The best managers I've had did less, not more - fewer meetings, clearer priorities, faster decisions. Micromanagement is fear wearing a productivity costume. I think most companies promote their best individual contributors into management and lose twice: a great builder gone, a mediocre manager gained. Trust is the only real productivity hack.`,
    finance: `Budgeting apps don't fix spending problems any more than scales fix eating problems - behavior beats tools every time. I think the personal finance industry profits from complexity, when the winning strategy fits on an index card: spend less than you earn, automate savings, buy boring index funds, wait. The hardest part isn't knowledge, it's patience.`,
    health: `We treat sleep like a luxury and then wonder why burnout is everywhere. I'm convinced most workplace wellness programs are box-ticking - a meditation app subscription doesn't fix a culture of 9pm emails. Walking meetings, actual lunch breaks, and respecting time off would do more than any perk. Health isn't a productivity hack, it's the foundation everything else sits on.`,
    climate: `Sustainability theater drives me crazy - companies celebrating paper straws while their supply chain burns coal. Real climate progress is boring: better insulation, efficient logistics, cleaner grids. I think the biggest lever isn't individual guilt, it's procurement - when big buyers demand cleaner suppliers, whole industries move. Progress beats purity.`,
    realestate: `Everyone asks when to buy like there's a magic date, but the honest answer is: when your life needs it and the math works. Rent versus buy isn't a moral question. I think we massively underbuild housing and then act shocked at prices - zoning reform would do more for affordability than any first-time-buyer subsidy. Location risk is the most underpriced factor in real estate.`,
  };
  const btn = $("generate-btn");
  const progress = $("progress");
  const progressText = $("progress-text");
  const results = $("results");
  const errorBox = $("error-box");
  const voiceBox = $("voice-box");
  const emptyState = $("empty-state");
  let polling = null;

  // Health check -> LLM badge
  fetch("/api/health")
    .then((r) => r.json())
    .then((h) => {
      const b = $("llm-badge");
      if (h.llm) { b.textContent = "AI mode"; b.classList.add("ok"); }
      else { b.textContent = "template mode"; b.title = "Set ANTHROPIC_API_KEY on the server for AI-written posts"; }
    })
    .catch(() => { $("llm-badge").textContent = "offline"; });

  // LinkedIn connection state
  let liConnected = false;
  const liBtn = $("li-connect");

  function refreshLinkedIn() {
    fetch("/api/linkedin/status")
      .then((r) => r.json())
      .then((s) => {
        if (!s.configured) { liBtn.style.display = "none"; return; }
        liBtn.style.display = "";
        liConnected = s.connected;
        if (s.connected) {
          liBtn.textContent = `LinkedIn: ${s.name} ✓ (disconnect)`;
          liBtn.onclick = () => {
            fetch("/api/linkedin/logout", { method: "POST" }).then(refreshLinkedIn);
          };
        } else {
          liBtn.textContent = "Connect LinkedIn";
          liBtn.onclick = () => { window.location.href = "/auth/linkedin"; };
        }
      })
      .catch(() => {});
  }
  refreshLinkedIn();

  // Surface OAuth errors passed back as ?li_error=...
  const liError = new URLSearchParams(window.location.search).get("li_error");
  if (liError) setError("LinkedIn sign-in failed: " + liError);

  function setError(msg) {
    errorBox.textContent = msg;
    errorBox.classList.toggle("visible", !!msg);
  }

  function setBusy(busy) {
    btn.disabled = busy;
    progress.classList.toggle("visible", busy);
    if (busy) { results.innerHTML = ""; voiceBox.classList.remove("visible"); emptyState.style.display = "none"; }
  }

  function esc(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function renderVoice(vp) {
    if (!vp || !vp.summary) return;
    let html = `<strong>Voice profile:</strong> ${esc(vp.summary)}`;
    if (vp.themes && vp.themes.length)
      html += `<br><strong>Themes:</strong> ${vp.themes.map(esc).join(" · ")}`;
    voiceBox.innerHTML = html;
    voiceBox.classList.add("visible");
  }

  function renderDrafts(drafts) {
    results.innerHTML = "";
    drafts.forEach((d, i) => {
      const card = document.createElement("div");
      card.className = "post-card";

      let imgHtml = "";
      let metaImg = "";
      if (d.image && d.image.src) {
        imgHtml = `<img class="post-image" src="${esc(d.image.src)}" alt="Suggested post image">`;
        metaImg = `<br>Image: ${esc(d.image.source)} — ${esc(d.image.license)} — ${esc(d.image.attribution)}` +
          (d.image.page ? ` (<a href="${esc(d.image.page)}" target="_blank" rel="noopener">image page</a>)` : "");
      }

      const srcLink = d.source && d.source.url
        ? `<a href="${esc(d.source.url)}" target="_blank" rel="noopener">${esc(d.source.title || d.source.url)}</a>`
        : esc((d.source && d.source.title) || "n/a");

      card.innerHTML = `
        <div class="post-header">
          <h3>Variant ${i + 1}</h3>
          <div style="display:flex; gap:8px;">
            <button class="btn btn-ghost btn-sm copy-btn">Copy text</button>
            <button class="btn btn-ghost btn-sm save-btn">Save to library</button>
            <button class="btn btn-primary btn-sm li-post-btn">Post to LinkedIn</button>
          </div>
        </div>
        ${imgHtml}
        <div class="post-body">${esc(d.text)}</div>
        <div class="post-meta">Based on: ${srcLink} (${esc((d.source && d.source.date) || "recent")})${metaImg}</div>`;

      const copyBtn = card.querySelector(".copy-btn");
      copyBtn.addEventListener("click", () => {
        navigator.clipboard.writeText(d.text).then(() => {
          copyBtn.textContent = "Copied!";
          copyBtn.classList.add("copied");
          setTimeout(() => {
            copyBtn.textContent = "Copy text";
            copyBtn.classList.remove("copied");
          }, 1800);
        });
      });

      const saveBtn = card.querySelector(".save-btn");
      saveBtn.addEventListener("click", async () => {
        saveBtn.disabled = true;
        try {
          const r = await fetch("/api/library/save", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(d),
          });
          if (!r.ok) throw new Error((await r.json()).error || "Save failed");
          saveBtn.textContent = "Saved ✓";
          saveBtn.classList.add("copied");
        } catch (e) {
          saveBtn.disabled = false;
          setError(e.message);
        }
      });

      const liPostBtn = card.querySelector(".li-post-btn");
      liPostBtn.addEventListener("click", async () => {
        if (!liConnected) {
          if (confirm("You need to connect your LinkedIn account first. Connect now?"))
            window.location.href = "/auth/linkedin";
          return;
        }
        if (!confirm("Post this draft to your LinkedIn profile? It will be publicly visible."))
          return;
        liPostBtn.disabled = true;
        liPostBtn.textContent = "Posting…";
        try {
          const r = await fetch("/api/linkedin/post", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              text: d.text,
              image_src: d.image ? d.image.src : "",
              draft_id: d.id || "",
            }),
          });
          const res = await r.json();
          if (!r.ok) throw new Error(res.error || "Posting failed");
          liPostBtn.textContent = res.with_image ? "Posted with image ✓" : "Posted ✓";
          liPostBtn.classList.add("copied");
        } catch (e) {
          liPostBtn.disabled = false;
          liPostBtn.textContent = "Post to LinkedIn";
          setError(e.message);
          if (String(e.message).includes("reconnect")) refreshLinkedIn();
        }
      });

      results.appendChild(card);
    });
  }

  function poll(jobId) {
    polling = setInterval(async () => {
      try {
        const r = await fetch(`/api/status/${jobId}`);
        const job = await r.json();
        if (job.progress) progressText.textContent = job.progress;
        if (job.status === "done") {
          clearInterval(polling);
          setBusy(false);
          renderVoice(job.result.voice_profile);
          renderDrafts(job.result.drafts);
          if (!job.result.llm_used)
            setError("Note: running in template mode (no ANTHROPIC_API_KEY on the server). Posts are basic templates, not AI-written.");
        } else if (job.status === "error") {
          clearInterval(polling);
          setBusy(false);
          emptyState.style.display = "";
          setError(job.error || "Generation failed.");
        }
      } catch (e) {
        clearInterval(polling);
        setBusy(false);
        emptyState.style.display = "";
        setError("Lost connection to the server.");
      }
    }, 1200);
  }

  // Topic dropdown fills the notes box with an editable sample
  $("sample-topic").addEventListener("change", () => {
    const key = $("sample-topic").value;
    if (!key) return;
    const box = $("transcript");
    if (box.value.trim() &&
        !confirm("Replace your current notes with the sample?")) {
      $("sample-topic").value = "";
      return;
    }
    box.value = SAMPLES[key] || "";
  });

  btn.addEventListener("click", async () => {
    setError("");
    const transcript = $("transcript").value.trim();
    if (!transcript) {
      setError("Paste some notes, or pick a sample topic from the dropdown.");
      return;
    }
    setBusy(true);
    progressText.textContent = "Starting…";
    try {
      const r = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          transcript,
          tone: $("tone").value,
          word_min: Math.max(0, Math.min(3000, parseInt($("wordmin").value, 10) || 100)),
          word_max: Math.max(0, Math.min(3000, parseInt($("wordmax").value, 10) || 200)),
          variants: parseInt($("variants").value, 10),
          lookback_value: parseInt($("lookback").value, 10) || 2,
          lookback_unit: $("lookback-unit").value,
          images: $("images").checked,
        }),
      });
      const data = await r.json();
      if (!r.ok || !data.job_id) throw new Error(data.error || "Request failed");
      poll(data.job_id);
    } catch (e) {
      setBusy(false);
      emptyState.style.display = "";
      setError(e.message || "Request failed.");
    }
  });
})();
