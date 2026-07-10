/* PostPilot generator page logic */
(() => {
  const $ = (id) => document.getElementById(id);
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
          <button class="btn btn-ghost btn-sm copy-btn">Copy text</button>
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

  btn.addEventListener("click", async () => {
    setError("");
    const transcript = $("transcript").value.trim();
    const useSample = $("use-sample").checked;
    if (!transcript && !useSample) {
      setError("Paste a transcript or check “Use built-in sample transcript”.");
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
          use_sample: useSample,
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
