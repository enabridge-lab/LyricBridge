// M2 web player — real-time word-by-word karaoke highlight.
//
// Consumes the server /transcribe contract directly (no server changes):
//   { duration_sec, words: [{text, start, end}], lrc: "[mm:ss.xx]line\n...", aligned }
// Plays an instrumental audio file and lights each word as the audio reaches it.
//
// The data-shaping logic is pure + exported so it unit-tests under node:test
// (see player.test.mjs). DOM wiring at the bottom is guarded so importing this
// module in node does not touch `document`.

// D4: resolve the backend URL. Order: <meta name="lyricbridge-api-base"> (set by
// the GitHub Pages build to the Modal URL) → http://localhost:8000 (self-host
// default). Pure + exported for node:test; guarded so it works without a DOM.
export function defaultApiBase(doc = (typeof document !== "undefined" ? document : null)) {
  const meta = doc && doc.querySelector('meta[name="lyricbridge-api-base"]');
  const v = meta && meta.getAttribute("content");
  return (v && v.trim()) || "http://localhost:8000";
}

// --- pure logic ------------------------------------------------------------

// Parse LRC "[mm:ss.xx]text" rows into [{start, text}] (line-level timestamps).
// Lines without a valid timestamp are skipped.
export function parseLrc(lrc) {
  const lines = [];
  for (const raw of (lrc || "").split("\n")) {
    const m = raw.match(/^\[(\d+):(\d+(?:\.\d+)?)\](.*)$/);
    if (!m) continue;
    const start = parseInt(m[1], 10) * 60 + parseFloat(m[2]);
    lines.push({ start, text: m[3] });
  }
  return lines;
}

// Bucket time-ordered words into lines using the LRC line start times as
// boundaries: a word belongs to the latest line whose start <= word.start.
// Reproduces the server's grouping (LRC starts ARE the first word of each line)
// without needing a separate structured field. Returns [{start, words:[...]}].
export function groupWordsIntoLines(words, lineStarts) {
  if (!words || !words.length) return [];
  if (!lineStarts || !lineStarts.length) {
    return [{ start: words[0].start, words: [...words] }];
  }
  const lines = lineStarts.map((s) => ({ start: s, words: [] }));
  let li = 0;
  for (const w of words) {
    while (li + 1 < lineStarts.length && lineStarts[li + 1] <= w.start) li++;
    lines[li].words.push(w);
  }
  // Drop lines that ended up with no words (e.g. duplicate LRC timestamps).
  return lines.filter((ln) => ln.words.length);
}

// Index of the word active at time `t`: the last word with start <= t, else -1
// (before the song's first word). Karaoke convention: a word stays lit until
// the next word starts. O(log n) binary search over time-ordered words.
export function activeWordIndex(words, t) {
  let lo = 0, hi = words.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (words[mid].start <= t) { ans = mid; lo = mid + 1; }
    else hi = mid - 1;
  }
  return ans;
}

// Apply a constant sync offset (ms) to the audio clock used for highlighting.
// A POSITIVE offset makes lyrics lead the audio (highlight earlier); negative
// makes them lag. We adjust only the COMPARISON time here, never word.start, so
// exported LRC/JSON timings stay exactly as transcribed.
export function withOffset(currentTime, offsetMs) {
  return currentTime + (Number(offsetMs) || 0) / 1000;
}

// F3: words with ASR confidence below this get the orange "not sure" underline.
// Segment-level confidence (every word in an ASR segment shares one score), so
// whole shaky phrases light up — which matches how melisma actually breaks ASR.
export const LOW_CONF = 0.55;

// True when a word should be flagged as low-confidence. Old payloads have no
// confidence field -> never flagged (null/undefined is "unknown", not "bad").
export function isLowConfidence(word, threshold = LOW_CONF) {
  return word?.confidence != null && word.confidence < threshold;
}

// F5: grade a payload's word-sync quality for the badge. Returns
// {level: "good"|"partial"|"rough", pct: number|null}, or null when the payload
// carries no sync info at all (old JSON files -> show no badge, never error).
// pct = % of ASR segments that got real forced alignment (vs interpolation).
export function syncQuality(payload) {
  if (!payload || payload.aligned == null) return null; // pre-sync-era payload
  if (payload.aligned === false) return { level: "rough", pct: null };
  const total = Number(payload.total_segment_count) || 0;
  if (total <= 0) return { level: "good", pct: null }; // aligned, no counts (e.g. hand-edited)
  const degraded = Number(payload.degraded_segment_count) || 0;
  const pct = Math.round(100 * (1 - degraded / total));
  return { level: pct >= 80 ? "good" : pct >= 40 ? "partial" : "rough", pct };
}

// Build the full {lines, words} view-model from a /transcribe payload.
export function buildModel(payload) {
  const words = Array.isArray(payload?.words) ? payload.words : [];
  const lineStarts = parseLrc(payload?.lrc).map((l) => l.start);
  const lines = groupWordsIntoLines(words, lineStarts);
  // Tag each word with a flat index so the highlighter can address it in O(1).
  let idx = 0;
  for (const ln of lines) for (const w of ln.words) w._i = idx++;
  return { words, lines };
}

// --- post-edit export (M4 correction path) ---------------------------------

function _fmtLrcTs(seconds) {
  const s = Math.max(seconds, 0);
  const m = Math.floor(s / 60);
  const sec = (s - m * 60).toFixed(2).padStart(5, "0");
  return `${String(m).padStart(2, "0")}:${sec}`;
}

// Rebuild LRC "[mm:ss.xx]line" from (possibly edited) lines.
export function serializeLrc(lines) {
  return lines
    .filter((ln) => ln.words.length)
    .map((ln) => `[${_fmtLrcTs(ln.words[0].start)}]${ln.words.map((w) => w.text).join("")}`)
    .join("\n");
}

// Flatten edited lines back to a /transcribe-shaped words[] (text/start/end only).
export function serializeWords(lines) {
  const out = [];
  for (const ln of lines) {
    for (const w of ln.words) out.push({ text: w.text, start: w.start, end: w.end });
  }
  return out;
}

// Re-export the whole edited payload in the same shape /transcribe returns, so a
// corrected file round-trips straight back into /render or the player.
export function serializePayload(lines, meta = {}) {
  const words = serializeWords(lines);
  return {
    language: meta.language ?? "th",
    duration_sec: meta.duration_sec ?? (words.length ? words[words.length - 1].end : 0),
    words,
    lrc: serializeLrc(lines),
    aligned: true, // hand-corrected
    edited: true,
  };
}

// Serialize edited lines to the POST /render/{job_id} body shape: an array of
// lines, each an array of {text,start,end}. Keeps the player's line breaks so
// the server doesn't have to re-guess them. Pure — round-trips with
// serializeWords (flattening this equals serializeWords(lines)).
export function serializeLines(lines) {
  return lines
    .filter((ln) => ln.words.length)
    .map((ln) => ln.words.map((w) => ({ text: w.text, start: w.start, end: w.end })));
}

// Set a word's start to `t` (tap-to-sync) and keep the line non-overlapping:
// clamp end to >= start, and push the previous word's end down if we moved past
// it. Returns nothing; mutates in place. Pure aside from the array it's given.
export function syncWordStart(words, i, t) {
  const w = words[i];
  if (!w) return;
  w.start = Math.max(0, t);
  if (w.end < w.start) w.end = w.start;
  if (i > 0 && words[i - 1].end > w.start) words[i - 1].end = w.start;
}

// --- server connection (optional convenience) ------------------------------

// POST a vocal stem to the live server's /transcribe and return the parsed
// payload (same shape buildModel expects). `fetchImpl` is injectable so this
// unit-tests without a network. Throws with the server's stage/error on failure.
export async function transcribeViaServer(file, apiBase, fetchImpl = fetch) {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("format", "json");
  const res = await fetchImpl(`${apiBase.replace(/\/$/, "")}/transcribe`, {
    method: "POST",
    body: fd,
  });
  if (!res.ok) {
    throw new Error(await _serverError(res));
  }
  return res.json();
}

// POST a FULL SONG to /karaoke -> {job_id, instrumental_url, words, lrc, ...}.
// One server round separates the vocals AND transcribes them; the caller then
// fetches `instrumental_url` for playback. `progressId` (optional) is sent so
// the server publishes per-stage progress at GET /progress/<id>. `fetchImpl`
// injectable for tests.
export async function karaokeViaServer(file, apiBase, fetchImpl = fetch, progressId = "") {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("lang", "th");
  if (progressId) fd.append("progress_id", progressId);
  const res = await fetchImpl(`${apiBase.replace(/\/$/, "")}/karaoke`, {
    method: "POST",
    body: fd,
  });
  if (!res.ok) {
    throw new Error(await _serverError(res));
  }
  return res.json();
}

// F4: submit a karaoke job to the async queue -> {job_id, status_url} (202).
// Returns null when the server predates the queue (404 -> caller falls back to
// the blocking /karaoke). Other failures (413/429/5xx) throw with stage/error.
export async function submitKaraokeJob(file, apiBase, fetchImpl = fetch, lang = "th") {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("lang", lang);
  const res = await fetchImpl(`${apiBase.replace(/\/$/, "")}/jobs/karaoke`, {
    method: "POST",
    body: fd,
  });
  if (res.status === 404) return null;
  if (!res.ok) {
    throw new Error(await _serverError(res));
  }
  return res.json();
}

// F4: poll GET /jobs/{id} until the job finishes. Resolves with the /karaoke-
// shaped result payload; throws "{stage}: {error}" when the job failed.
// `onUpdate` receives every status body (queue_position + stage) for the UI;
// `sleep` is injectable so tests run without real timers.
export async function pollKaraokeJob(jobId, apiBase, {
  fetchImpl = fetch,
  intervalMs = 1500,
  sleep = (ms) => new Promise((r) => setTimeout(r, ms)),
  onUpdate = null,
} = {}) {
  const base = apiBase.replace(/\/$/, "");
  for (;;) {
    const res = await fetchImpl(`${base}/jobs/${jobId}`);
    if (!res.ok) {
      throw new Error(await _serverError(res));
    }
    const st = await res.json();
    if (onUpdate) onUpdate(st);
    if (st.status === "done") return st.result;
    if (st.status === "error") {
      const e = st.error || {};
      throw new Error(`${e.stage || "server"}: ${e.error || "job failed"}`);
    }
    await sleep(intervalMs);
  }
}

// F4: remember/recall the in-flight job so a refreshed page resumes polling
// instead of losing the run. `storage` injectable (tests pass a fake Map-like;
// the browser passes localStorage).
const JOB_REF_KEY = "lyricbridgeJob";
export function saveJobRef(storage, jobId, base) {
  try { storage.setItem(JOB_REF_KEY, JSON.stringify({ jobId, base })); } catch { /* private mode */ }
}
export function loadJobRef(storage) {
  try {
    const v = JSON.parse(storage.getItem(JOB_REF_KEY) || "null");
    return v && v.jobId && v.base ? v : null;
  } catch {
    return null;
  }
}
export function clearJobRef(storage) {
  try { storage.removeItem(JOB_REF_KEY); } catch { /* ignore */ }
}

// POST edited lines to /render/{job_id} -> mp4 Blob (F2: re-render without
// re-uploading the instrumental — it's still parked on the server's job store).
// `fetchImpl` injectable for tests. Throws with the server's stage/error.
// `style` (F8, optional): flat AssStyle fields, merged into the JSON body.
export async function renderVideoViaServer(jobId, lines, apiBase, fetchImpl = fetch, style = null) {
  const res = await fetchImpl(`${apiBase.replace(/\/$/, "")}/render/${jobId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lines, ...(style || {}) }),
  });
  if (!res.ok) {
    throw new Error(await _serverError(res));
  }
  return res.blob();
}

// F8: normalize raw UI values into the flat style fields POST /render/{job_id}
// accepts. Pure + forgiving: junk values are dropped (server defaults apply),
// "#RRGGBB" colour-picker values lose the "#".
export function buildRenderStyle({ font, fontSize, primary, highlight, alignment } = {}) {
  const style = {};
  const hex = (v) =>
    typeof v === "string" && /^#?[0-9a-fA-F]{6}$/.test(v)
      ? v.replace(/^#/, "").toUpperCase()
      : null;
  if (font && typeof font === "string") style.font = font;
  const size = parseInt(fontSize, 10);
  if (Number.isFinite(size)) style.font_size = size;
  const p = hex(primary);
  if (p) style.primary_colour = p;
  const h = hex(highlight);
  if (h) style.highlight_colour = h;
  const al = parseInt(alignment, 10);
  if (Number.isFinite(al)) style.alignment = al;
  return style;
}

// Map a server stage name to a {th, en, step} label for the progress UI.
export function stageLabel(stage) {
  const m = {
    queued:       { th: "กำลังรอคิว…", en: "Waiting in queue", step: 0 },
    separating:   { th: "กำลังแยกเสียงร้องออกจากดนตรี…", en: "Separating vocals from the music", step: 1 },
    transcribing: { th: "AI กำลังฟังและถอดเป็นเนื้อเพลง…", en: "Listening & transcribing the lyrics", step: 2 },
    aligning:     { th: "กำลังจับเวลาเนื้อร้องทีละคำ…", en: "Aligning each word to the audio", step: 3 },
    building:     { th: "กำลังสร้างไฟล์คาราโอเกะ…", en: "Building the karaoke file", step: 4 },
    done:         { th: "เกือบเสร็จแล้ว…", en: "Almost done", step: 4 },
  };
  return m[stage] || { th: "กำลังประมวลผล…", en: "Processing", step: 0 };
}

// Pull a "{stage}: {error}" message out of a failed JSON response body.
async function _serverError(res) {
  let detail = `HTTP ${res.status}`;
  try {
    const j = await res.json();
    if (j?.error) detail = `${j.stage || "server"}: ${j.error}`;
  } catch {
    /* non-JSON error body */
  }
  return detail;
}

// --- DOM wiring (browser only) --------------------------------------------

function init() {
  const $ = (id) => document.getElementById(id);
  const els = {
    audio: $("audio"),
    audioFile: $("audioFile"),
    jsonFile: $("jsonFile"),
    lyrics: $("lyrics"),
    status: $("status"),
    editToggle: $("editToggle"),
    romanToggle: $("romanToggle"),
    exportLrc: $("exportLrc"),
    exportJson: $("exportJson"),
    renderVideo: $("renderVideo"),
    // F8: video style controls
    renderStylePanel: $("renderStylePanel"),
    styleFont: $("styleFont"), styleSize: $("styleSize"),
    stylePrimary: $("stylePrimary"), styleHighlight: $("styleHighlight"),
    stylePosition: $("stylePosition"),
    apiBase: $("apiBase"),
    vocalFile: $("vocalFile"),
    // friendly-UI extras (optional; guarded so headless/old markup still works)
    audioName: $("audioName"), jsonName: $("jsonName"), vocalName: $("vocalName"),
    audioDrop: $("audioDrop"), jsonDrop: $("jsonDrop"), vocalDrop: $("vocalDrop"),
    step1: $("step1"), step2: $("step2"),
    // one-upload flow (Step 0)
    songFile: $("songFile"), songName: $("songName"),
    songDrop: $("songDrop"), step0: $("step0"),
    // sync offset (§3) + nudge (§5)
    syncOffset: $("syncOffset"), syncOffsetVal: $("syncOffsetVal"),
    nudgeBack: $("nudgeBack"), nudgeFwd: $("nudgeFwd"),
    // vocal guide
    syncBadge: $("syncBadge"),
    vocalGuidePanel: $("vocalGuidePanel"),
    vocalGuideToggle: $("vocalGuideToggle"),
    vocalVolume: $("vocalVolume"),
    vocalVolumeVal: $("vocalVolumeVal"),
    vocalSliderRow: $("vocalSliderRow"),
  };

  // D4: on a hosted build the meta carries the Modal URL — pre-fill the (still
  // editable) server field so a visitor needs zero setup. Self-host leaves the
  // meta empty, so this keeps the HTML's localhost default untouched.
  const _apiDefault = defaultApiBase();
  if (els.apiBase && _apiDefault !== "http://localhost:8000") {
    els.apiBase.value = _apiDefault;
  }

  // D5: surface the backend's $30 kill switch. If /healthz reports
  // accepting:false, show a banner and disable upload. Best-effort — a network
  // error or a pre-D5 server (no `accepting` field) simply shows nothing.
  (async () => {
    try {
      const base = (els.apiBase?.value || defaultApiBase()).trim().replace(/\/$/, "");
      const res = await fetch(`${base}/healthz`);
      if (!res.ok) return;
      const h = await res.json();
      if (h && h.accepting === false) {
        const b = document.createElement("div");
        b.id = "demoPausedBanner";
        b.textContent = "⚠️ เดโมปิดชั่วคราว — เต็มโควต้าเดือนนี้ ลองใหม่เดือนหน้า หรือ self-host";
        b.style.cssText =
          "background:#b00020;color:#fff;padding:.6rem 1rem;text-align:center;font-weight:600";
        document.body.prepend(b);
        if (els.songFile) els.songFile.disabled = true;
      }
    } catch { /* offline / pre-D5 server — no banner */ }
  })();

  let model = { words: [], lines: [] };
  let meta = {};
  // F2: the /karaoke job whose instrumental is still parked on the server —
  // lets the render button POST /render/{job_id} without re-uploading audio.
  let renderJobId = null;
  let renderApiBase = "";
  let wordSpans = [];
  let lastActive = -1;
  let editMode = false;
  let vocalAudio = null;
  let vocalGuideVol = Number(localStorage.getItem("vocalGuideVol") ?? "") || 0.3;
  // Constant sync offset (ms), persisted per browser. + = lyrics lead the audio.
  let syncOffsetMs = Number(localStorage.getItem("syncOffsetMs")) || 0;

  // --- sync offset slider (§3) ---
  if (els.syncOffset) {
    els.syncOffset.value = String(syncOffsetMs);
    if (els.syncOffsetVal) els.syncOffsetVal.textContent = syncOffsetMs + " ms";
    els.syncOffset.addEventListener("input", (e) => {
      syncOffsetMs = Number(e.target.value) || 0;
      if (els.syncOffsetVal) els.syncOffsetVal.textContent = syncOffsetMs + " ms";
      localStorage.setItem("syncOffsetMs", String(syncOffsetMs));
      lastActive = -1; // force the highlight to re-evaluate immediately
    });
  }

  // mark a step card "done" + show the chosen filename on its dropzone
  function markLoaded(step, dropEl, nameEl, file) {
    step?.classList.add("done");
    dropEl?.classList.add("filled");
    if (nameEl && file) nameEl.textContent = "✓ " + file.name;
  }

  els.jsonFile.addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
      const payload = JSON.parse(await file.text());
      loadModel(payload, file);
    } catch (err) {
      setStatus("อ่านไฟล์เนื้อเพลงไม่ได้ · Could not read lyrics file: " + err.message, "error");
    }
  });

  // shared: install a transcribe payload into the player + update the UI state
  function loadModel(payload, file) {
    model = buildModel(payload);
    meta = { language: payload.language, duration_sec: payload.duration_sec };
    renderLyrics();
    showSyncBadge(payload);
    markLoaded(els.step2, els.jsonDrop, els.jsonName, file);
    document.body.classList.add("has-lyrics");
    // F3: point the user at the words the AI itself doubts.
    const lowCount = model.words.filter((w) => isLowConfidence(w)).length;
    const lowNote = lowCount
      ? ` · มี ${lowCount} คำที่ AI ไม่มั่นใจ (ขีดเส้นใต้สีส้ม) — เปิดโหมดแก้ไขเพื่อตรวจ`
      : "";
    setStatus(
      `พร้อมแล้ว! ${model.words.length} คำ · ${model.lines.length} บรรทัด` +
        (payload.aligned ? " · ตรงจังหวะรายคำ" : " · จับเวลาแบบประโยค") +
        lowNote +
        `  ·  Ready — ${model.words.length} words`,
      "ok"
    );
  }

  // F5: badge that sets the "how accurate is the timing?" expectation before
  // the user sings. Built via textContent (XSS-safe convention of this file).
  function showSyncBadge(payload) {
    if (!els.syncBadge) return;
    const q = syncQuality(payload);
    if (!q) {
      els.syncBadge.hidden = true; // old payload without sync info -> no badge
      return;
    }
    const pctTxt = q.pct != null ? ` ${q.pct}%` : "";
    const text = {
      good: `🟢 จังหวะแม่น${pctTxt} · Word-synced`,
      partial: `🟡 จังหวะโดยประมาณ${pctTxt} · Partially synced — เปิดโหมดแก้ไขช่วยปรับได้`,
      rough: `🔴 จังหวะประมาณเท่านั้น · Estimated timing — แนะนำใช้โหมดแก้ไข`,
    }[q.level];
    els.syncBadge.textContent = text;
    els.syncBadge.className = "sync-badge " + q.level;
    els.syncBadge.hidden = false;
  }

  function _wireVocalSync() {
    if (!vocalAudio) return;
    els.audio.addEventListener("seeked", () => {
      if (!vocalAudio) return;
      vocalAudio.currentTime = els.audio.currentTime;
    });
    els.audio.addEventListener("play", () => {
      if (!vocalAudio) return;
      vocalAudio.currentTime = els.audio.currentTime;
      vocalAudio.play().catch(() => {/* autoplay policy — ignored */});
    });
    els.audio.addEventListener("pause", () => {
      vocalAudio?.pause();
    });
  }

  function loadVocalGuide(blobOrUrl) {
    if (vocalAudio) {
      vocalAudio.pause();
      if (vocalAudio.src?.startsWith("blob:")) URL.revokeObjectURL(vocalAudio.src);
      vocalAudio = null;
    }
    vocalAudio = new Audio();
    vocalAudio.src = typeof blobOrUrl === "string"
      ? blobOrUrl
      : URL.createObjectURL(blobOrUrl);
    vocalAudio.volume = vocalGuideVol;
    _wireVocalSync();
    if (els.vocalGuidePanel) {
      els.vocalGuidePanel.hidden = false;
      if (els.vocalVolume) {
        els.vocalVolume.value = String(Math.round(vocalGuideVol * 100));
        if (els.vocalVolumeVal) els.vocalVolumeVal.textContent = Math.round(vocalGuideVol * 100) + "%";
      }
      if (els.vocalGuideToggle) els.vocalGuideToggle.checked = false;
      vocalAudio.volume = 0;
      if (els.vocalSliderRow) els.vocalSliderRow.style.opacity = "0.4";
    }
  }

  // --- ONE-UPLOAD flow (Step 0): full song -> separate + transcribe + play ---
  if (els.songFile) {
    els.songFile.addEventListener("change", (e) => runKaraoke(e.target.files[0]));
  }

  async function runKaraoke(file) {
    if (!file) return;
    const base = (els.apiBase?.value || defaultApiBase()).trim();
    if (els.songName) els.songName.textContent = "⏳ " + file.name;
    if (els.songFile) els.songFile.disabled = true;

    const fileMB = (file.size / 1024 / 1024).toFixed(2);
    console.group(`[LyricBridge] runKaraoke — ${file.name}`);
    console.log("file:", file.name, `${fileMB} MB`, file.type || "(no MIME type)");
    console.log("API base:", base);
    console.log("browser:", navigator.userAgent);

    // Pre-flight: confirm the server is reachable before starting the big upload.
    try {
      const ping = await fetch(base.replace(/\/$/, "") + "/healthz");
      console.log("✅ /healthz reachable — status", ping.status);
    } catch (pingErr) {
      console.error("❌ /healthz UNREACHABLE:", pingErr);
      setStatus(
        `เชื่อมต่อเซิร์ฟเวอร์ไม่ได้ · Cannot reach server at ${base} — ${pingErr.message}` +
        " | ตรวจสอบว่า server กำลังทำงานอยู่ (check Console F12)",
        "error"
      );
      if (els.songName) els.songName.textContent = "";
      if (els.songFile) els.songFile.disabled = false;
      console.groupEnd();
      return;
    }

    try {
      // F4: prefer the async queue — submit returns in seconds, then we poll
      // GET /jobs/{id}. The job survives a page refresh (see the resume block
      // at the bottom of init). A pre-F4 server 404s the submit -> fall back
      // to the legacy blocking /karaoke.
      console.time("[LyricBridge] karaoke job");
      let payload;
      const submitted = await submitKaraokeJob(file, base);
      if (submitted) {
        console.log("→ queued as job", submitted.job_id);
        saveJobRef(localStorage, submitted.job_id, base);
        showJobUpdate({ status: "queued" });
        try {
          payload = await pollKaraokeJob(submitted.job_id, base, { onUpdate: showJobUpdate });
        } finally {
          clearJobRef(localStorage);
        }
      } else {
        console.log("→ no /jobs/karaoke on this server — using blocking /karaoke");
        payload = await legacyKaraoke(file, base, fileMB);
      }
      console.timeEnd("[LyricBridge] karaoke job");
      console.log("← karaoke OK:", payload);
      await installKaraokeResult(payload, base, file);
    } catch (err) {
      const errType = err?.constructor?.name || "Error";
      console.error(`[LyricBridge] karaoke failed (${errType}):`, err);
      console.log("  err.message :", err.message);
      console.log("  err.stack   :", err.stack);
      console.log("Tip: Network tab → find POST /jobs/karaoke (or /karaoke) → check Status + Response");
      if (els.songName) els.songName.textContent = "";
      clearProcessing();
      const detail = `[${errType}] ${err.message} — open Console (F12) for full trace`;
      setStatus("ทำคาราโอเกะไม่สำเร็จ · Karaoke failed — " + detail, "error");
    } finally {
      if (els.songFile) els.songFile.disabled = false;
      console.groupEnd();
    }
  }

  // Legacy path for servers without the F4 queue: one blocking POST /karaoke
  // with the old /progress side-poll for the stage indicator.
  async function legacyKaraoke(file, base, fileMB) {
    const progressId =
      (crypto.randomUUID && crypto.randomUUID()) || String(Date.now()) + Math.random();
    showStage("separating");
    const poll = setInterval(async () => {
      try {
        const r = await fetch(base.replace(/\/$/, "") + "/progress/" + progressId);
        if (!r.ok) return;
        const p = await r.json();
        if (p && p.stage && p.stage !== "unknown") showStage(p.stage);
      } catch { /* transient poll error -> keep last stage */ }
    }, 1200);
    try {
      console.log("→ POST", base.replace(/\/$/, "") + "/karaoke", `(${fileMB} MB upload, starting…)`);
      return await karaokeViaServer(file, base, fetch, progressId);
    } finally {
      clearInterval(poll);
    }
  }

  // Shared tail of the karaoke flow: instrumental + lyrics + vocal guide into
  // the player. `file` is null when resuming a job after a page refresh.
  async function installKaraokeResult(payload, base, file) {
    showStage("fetching");
    // Set audio.src directly — the browser's native audio engine streams the
    // stem (now a ~3-4 MB m4a; WAV only if the server's encode fell back) and
    // issues range requests when the user seeks.
    const instrumentalSrc = base.replace(/\/$/, "") + payload.instrumental_url;
    console.log("→ loading instrumental via audio.src:", instrumentalSrc);
    await new Promise((resolve, reject) => {
      els.audio.addEventListener("canplay", resolve, { once: true });
      els.audio.addEventListener("error", () =>
        reject(new Error("audio load failed (code " + (els.audio.error?.code ?? "?") + ")")),
        { once: true }
      );
      els.audio.src = instrumentalSrc;
      els.audio.load();
    });
    console.log("← instrumental ready");
    document.body.classList.add("has-audio");
    markLoaded(els.step0, els.songDrop, els.songName, file);
    loadModel(payload); // renders lyrics + reveals tools
    // Remember the job so the render-video button can reuse the parked
    // instrumental (server renews its TTL on every access).
    renderJobId = payload.job_id || null;
    renderApiBase = base;
    if (els.renderVideo) els.renderVideo.hidden = !renderJobId;
    if (els.renderStylePanel) els.renderStylePanel.hidden = !renderJobId; // F8
    // Vocal guide: set src directly (same native streaming path as above)
    if (payload.vocal_url) {
      try {
        loadVocalGuide(base.replace(/\/$/, "") + payload.vocal_url);
      } catch {
        // Vocal load failed — degrade silently (guide panel stays hidden)
      }
    }
  }

  // F4: a job-status body -> the big stage indicator. Queued jobs show their
  // position; running jobs reuse the normal 4-step stage display.
  function showJobUpdate(st) {
    if (st.status === "queued") {
      const pos = st.queue_position > 1 ? ` (คิวที่ ${st.queue_position})` : "";
      showProcessing("กำลังรอคิว…" + pos, "Waiting in queue", 0);
      setStatus(`(0/4) กำลังรอคิว${pos} · Waiting in queue`, "busy");
    } else if (st.stage) {
      showStage(st.stage);
    }
  }

  // Render a stage with a 4-step progress bar in the lyrics area + status line.
  function showStage(stage) {
    if (stage === "fetching") {
      showProcessing("กำลังโหลดดนตรี (instrumental)…", "Loading the music track", 4);
      setStatus("กำลังโหลดดนตรี… · Loading music", "busy");
      return;
    }
    const lab = stageLabel(stage);
    showProcessing(lab.th, lab.en, lab.step);
    setStatus(`(${lab.step}/4) ${lab.th} · ${lab.en}`, "busy");
  }

  // --- transcribe a vocal stem via the live server (no JSON file needed) ---
  if (els.vocalFile) {
    els.vocalFile.addEventListener("change", async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      const base = (els.apiBase?.value || defaultApiBase()).trim();
      if (els.vocalName) els.vocalName.textContent = "⏳ " + file.name;
      setStatus("AI กำลังถอดเนื้อเพลง… (ครั้งแรกอาจช้าเพราะโหลดโมเดล) · Transcribing…", "busy");
      showProcessing();
      try {
        const payload = await transcribeViaServer(file, base);
        loadModel(payload, file);
      } catch (err) {
        if (els.vocalName) els.vocalName.textContent = "";
        clearProcessing();
        setStatus("ถอดเนื้อไม่สำเร็จ · Transcribe failed — " + err.message, "error");
      }
    });
  }

  // --- post-edit (M4) ---
  els.editToggle.addEventListener("change", (e) => {
    editMode = e.target.checked;
    els.lyrics.classList.toggle("editing", editMode);
    setStatus(
      editMode
        ? "โหมดแก้ไข: คลิกคำเพื่อตั้งเวลา · ดับเบิลคลิกเพื่อแก้คำ · Edit mode on"
        : ""
    );
  });

  // F7: show/hide romanized readings via a body class — no lyric re-render.
  if (els.romanToggle) {
    const showRoman = localStorage.getItem("showRoman") === "1";
    els.romanToggle.checked = showRoman;
    document.body.classList.toggle("show-roman", showRoman);
    els.romanToggle.addEventListener("change", (e) => {
      document.body.classList.toggle("show-roman", e.target.checked);
      localStorage.setItem("showRoman", e.target.checked ? "1" : "0");
    });
  }

  els.exportLrc.addEventListener("click", () =>
    download("lyrics.edited.lrc", serializeLrc(model.lines), "text/plain")
  );
  els.exportJson.addEventListener("click", () =>
    download(
      "lyrics.edited.json",
      JSON.stringify(serializePayload(model.lines, meta), null, 2),
      "application/json"
    )
  );

  function download(name, data, type) {
    const blob = data instanceof Blob ? data : new Blob([data], { type });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  }

  // F8: read the style controls -> flat style fields for the render request.
  function currentRenderStyle() {
    return buildRenderStyle({
      font: els.styleFont?.value,
      fontSize: els.styleSize?.value,
      primary: els.stylePrimary?.value,
      highlight: els.styleHighlight?.value,
      alignment: els.stylePosition?.value,
    });
  }

  // F8: remember the user's style across sessions.
  try {
    const saved = JSON.parse(localStorage.getItem("renderStyle") || "null");
    if (saved) {
      if (els.styleFont && saved.font) els.styleFont.value = saved.font;
      if (els.styleSize && saved.font_size) els.styleSize.value = String(saved.font_size);
      if (els.stylePrimary && saved.primary_colour) els.stylePrimary.value = "#" + saved.primary_colour.toLowerCase();
      if (els.styleHighlight && saved.highlight_colour) els.styleHighlight.value = "#" + saved.highlight_colour.toLowerCase();
      if (els.stylePosition && saved.alignment) els.stylePosition.value = String(saved.alignment);
    }
  } catch { /* corrupt saved style -> defaults */ }

  // F2: burn the (possibly edited) lyrics over the parked instrumental.
  els.renderVideo?.addEventListener("click", async () => {
    if (!renderJobId || !model.lines.length) return;
    els.renderVideo.disabled = true;
    setStatus("กำลังเผาวิดีโอ… อาจใช้เวลา 1-3 นาที · Rendering video, this can take 1-3 minutes", "busy");
    try {
      const style = currentRenderStyle(); // F8
      localStorage.setItem("renderStyle", JSON.stringify(style));
      const blob = await renderVideoViaServer(
        renderJobId, serializeLines(model.lines), renderApiBase, fetch, style
      );
      download("karaoke.mp4", blob);
      setStatus("ได้วิดีโอแล้ว! · Video downloaded — karaoke.mp4", "ok");
    } catch (err) {
      setStatus("สร้างวิดีโอไม่สำเร็จ · Render failed — " + err.message, "error");
    } finally {
      els.renderVideo.disabled = false;
    }
  });

  // F3: an edited word is user-confirmed — drop the "AI not sure" flag.
  function confirmWord(w, span) {
    w.confidence = null;
    if (span) {
      span.classList.remove("low-conf");
      span.removeAttribute("title");
    }
  }

  // F6: the user set this word's time themselves — it's no longer a guess.
  function confirmTiming(w, span) {
    w.interpolated = false;
    span?.classList.remove("interp");
  }

  function editWord(w, span) {
    if (!editMode) return;
    // Tap-to-sync: pin this word's start to where the audio is now.
    syncWordStart(model.words, w._i, els.audio.currentTime);
    span.classList.add("edited");
    confirmWord(w, span);
    confirmTiming(w, span);
    setStatus(`synced "${w.text}" → ${w.start.toFixed(2)}s`);
  }

  function retypeWord(w, span) {
    if (!editMode) return;
    const next = window.prompt("แก้คำนี้ · Fix this word:", w.text);
    if (next != null && next !== w.text) {
      w.text = next;
      w.roman = null; // F7: the old romanization is stale for the new text
      span.textContent = next; // also drops the <small class="roman"> child
      span.classList.add("edited");
      confirmWord(w, span);
    }
  }

  // §5: stamp the currently-highlighted word to the playhead (LRC-maker style).
  // Use the RAW currentTime (not withOffset): syncOffsetMs is a display-only
  // highlight lead/lag, and word.start is exported -- baking the offset in would
  // corrupt the LRC/JSON by the offset amount. Matches editWord (click-to-sync).
  function stampActiveWord() {
    const i = lastActive;
    if (i < 0 || !model.words[i]) return;
    syncWordStart(model.words, i, els.audio.currentTime);
    wordSpans[i]?.classList.add("edited");
    confirmWord(model.words[i], wordSpans[i]);
    confirmTiming(model.words[i], wordSpans[i]);
    setStatus(`ตอกเวลา "${model.words[i].text}" → ${model.words[i].start.toFixed(2)}s · stamped`, "ok");
  }

  // §5: nudge the active word's start by ±ms (fine alignment of melisma).
  function nudgeActiveWord(deltaMs) {
    const i = lastActive;
    if (i < 0 || !model.words[i]) return;
    syncWordStart(model.words, i, model.words[i].start + deltaMs / 1000);
    wordSpans[i]?.classList.add("edited");
    confirmTiming(model.words[i], wordSpans[i]);
    setStatus(`เลื่อน "${model.words[i].text}" ${deltaMs > 0 ? "+" : ""}${deltaMs}ms → ${model.words[i].start.toFixed(2)}s`, "ok");
  }

  // Space stamps the active word while in edit mode (ignore when typing in a field).
  document.addEventListener("keydown", (e) => {
    if (!editMode) return;
    const tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea") return;
    if (e.code === "Space" || e.key === " ") {
      e.preventDefault();
      stampActiveWord();
    }
  });
  els.nudgeBack?.addEventListener("click", () => nudgeActiveWord(-50));
  els.nudgeFwd?.addEventListener("click", () => nudgeActiveWord(50));

  // --- vocal guide panel ---
  if (els.vocalGuideToggle) {
    els.vocalGuideToggle.addEventListener("change", (e) => {
      if (!vocalAudio) return;
      const on = e.target.checked;
      if (on) {
        vocalAudio.volume = vocalGuideVol;
        if (els.vocalSliderRow) els.vocalSliderRow.style.opacity = "1";
      } else {
        vocalAudio.volume = 0;
        if (els.vocalSliderRow) els.vocalSliderRow.style.opacity = "0.4";
      }
    });
  }

  if (els.vocalVolume) {
    els.vocalVolume.value = String(Math.round(vocalGuideVol * 100));
    els.vocalVolume.addEventListener("input", (e) => {
      vocalGuideVol = Number(e.target.value) / 100;
      if (els.vocalVolumeVal) els.vocalVolumeVal.textContent = e.target.value + "%";
      localStorage.setItem("vocalGuideVol", String(vocalGuideVol));
      if (vocalAudio && els.vocalGuideToggle?.checked) {
        vocalAudio.volume = vocalGuideVol;
      }
    });
  }

  els.audioFile.addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (!file) return;
    loadAudio(file);
  });

  function loadAudio(file) {
    els.audio.src = URL.createObjectURL(file);
    markLoaded(els.step1, els.audioDrop, els.audioName, file);
    document.body.classList.add("has-audio");
    setStatus("โหลดเพลงแล้ว — กดเล่นได้เลย · Song loaded — press play", "ok");
  }

  // Drag & drop: drop a file on a zone -> behave like choosing it.
  function wireDrop(zoneEl, inputEl, onFile) {
    if (!zoneEl || !inputEl) return;
    ["dragenter", "dragover"].forEach((ev) =>
      zoneEl.addEventListener(ev, (e) => {
        e.preventDefault();
        zoneEl.classList.add("dragover");
      })
    );
    ["dragleave", "drop"].forEach((ev) =>
      zoneEl.addEventListener(ev, (e) => {
        e.preventDefault();
        zoneEl.classList.remove("dragover");
      })
    );
    zoneEl.addEventListener("drop", (e) => {
      const file = e.dataTransfer?.files?.[0];
      if (file) onFile(file);
    });
  }
  wireDrop(els.songDrop, els.songFile, runKaraoke);
  wireDrop(els.audioDrop, els.audioFile, loadAudio);
  wireDrop(els.jsonDrop, els.jsonFile, async (file) => {
    try {
      loadModel(JSON.parse(await file.text()), file);
    } catch (err) {
      setStatus("อ่านไฟล์เนื้อเพลงไม่ได้ · Could not read lyrics file: " + err.message, "error");
    }
  });

  // Big visible "AI is working" state in the lyrics area. Built via DOM/
  // textContent (no innerHTML injection) so it's XSS-safe even if reused.
  // `step` (0-4) draws a 4-dot progress bar of the named stages.
  function showProcessing(
    thaiMsg = "AI กำลังถอดเนื้อเพลง…",
    enMsg = "Transcribing on the server — this can take a few minutes, please don't refresh",
    step = null
  ) {
    els.lyrics.replaceChildren();
    const box = document.createElement("div");
    box.className = "empty processing";
    const icon = document.createElement("span");
    icon.className = "empty-icon spin";
    icon.textContent = "⏳";
    const p = document.createElement("p");
    p.textContent = thaiMsg;
    const small = document.createElement("small");
    small.textContent = enMsg + " · อย่าเพิ่งรีเฟรชหน้านี้";
    p.appendChild(small);
    box.append(icon, p);
    if (step !== null) box.appendChild(_stepBar(step));
    els.lyrics.appendChild(box);
  }

  // A 4-step progress bar: ① แยกเสียง ② ถอดเนื้อ ③ จับเวลา ④ สร้างไฟล์.
  function _stepBar(step) {
    const labels = ["แยกเสียง", "ถอดเนื้อ", "จับเวลา", "สร้างไฟล์"];
    const bar = document.createElement("div");
    bar.className = "stepbar";
    labels.forEach((label, k) => {
      const n = k + 1;
      const dot = document.createElement("div");
      dot.className = "stepdot" + (n < step ? " done" : n === step ? " active" : "");
      const num = document.createElement("span");
      num.className = "stepnum";
      num.textContent = n < step ? "✓" : String(n);
      const txt = document.createElement("span");
      txt.className = "steptxt";
      txt.textContent = label;
      dot.append(num, txt);
      bar.appendChild(dot);
    });
    return bar;
  }
  function clearProcessing() {
    const p = els.lyrics.querySelector(".processing");
    if (p) els.lyrics.innerHTML = "";
  }

  function renderLyrics() {
    els.lyrics.innerHTML = "";
    wordSpans = [];
    lastActive = -1;
    for (const ln of model.lines) {
      const lineEl = document.createElement("div");
      lineEl.className = "line";
      for (const w of ln.words) {
        const span = document.createElement("span");
        span.className = "word";
        span.textContent = w.text;
        // F3: flag words the ASR itself wasn't sure about (orange wavy underline
        // — "uncertain", not "wrong"). Tooltip shows the model's confidence.
        if (isLowConfidence(w)) {
          span.classList.add("low-conf");
          span.title = `AI มั่นใจ ${Math.round(w.confidence * 100)}% · ASR confidence`;
        }
        // F6: faded = this word's TIMING is guessed (different axis from F3's
        // orange underline = TEXT uncertain; both can apply to one word).
        if (w.interpolated) span.classList.add("interp");
        // F7: romanized reading; hidden/shown purely via the body class so the
        // toggle never re-renders the lyrics.
        if (w.roman) {
          const r = document.createElement("small");
          r.className = "roman";
          r.textContent = w.roman;
          span.appendChild(r);
        }
        span.addEventListener("click", () => editWord(w, span));
        span.addEventListener("dblclick", () => retypeWord(w, span));
        wordSpans[w._i] = span;
        lineEl.appendChild(span);
      }
      els.lyrics.appendChild(lineEl);
    }
  }

  function highlight() {
    if (model.words.length) {
      const i = activeWordIndex(model.words, withOffset(els.audio.currentTime, syncOffsetMs));
      if (i !== lastActive) {
        if (wordSpans[lastActive]) wordSpans[lastActive].classList.remove("active");
        const span = wordSpans[i];
        if (span) {
          span.classList.add("active");
          // Keep the current line in view without yanking on every word.
          if (!wordSpans[lastActive] || wordSpans[lastActive].parentElement !== span.parentElement) {
            span.parentElement.scrollIntoView({ behavior: "smooth", block: "center" });
          }
        }
        lastActive = i;
      }
    }
    requestAnimationFrame(highlight);
  }

  function setStatus(msg, kind) {
    els.status.textContent = msg;
    els.status.className = "status" + (kind ? " " + kind : "");
  }

  requestAnimationFrame(highlight);

  // F4: if a job was still running when the page was refreshed, resume polling
  // it instead of losing the run (fixes the old "don't refresh!" pain).
  (async () => {
    const saved = loadJobRef(localStorage);
    if (!saved) return;
    try {
      console.log("[LyricBridge] resuming job", saved.jobId);
      setStatus("กำลังติดตามงานเดิมต่อ… · Resuming your previous job", "busy");
      showJobUpdate({ status: "queued" });
      const payload = await pollKaraokeJob(saved.jobId, saved.base, { onUpdate: showJobUpdate });
      clearJobRef(localStorage);
      await installKaraokeResult(payload, saved.base, null);
    } catch (err) {
      // Stale/expired/failed job — clear the ref so we don't retry forever.
      clearJobRef(localStorage);
      clearProcessing();
      setStatus("งานเดิมหมดอายุหรือไม่สำเร็จ · Previous job expired or failed — " + err.message, "error");
    }
  })();
}

if (typeof document !== "undefined") {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
}
