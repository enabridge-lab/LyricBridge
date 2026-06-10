// M2 web player — real-time word-by-word karaoke highlight.
//
// Consumes the server /transcribe contract directly (no server changes):
//   { duration_sec, words: [{text, start, end}], lrc: "[mm:ss.xx]line\n...", aligned }
// Plays an instrumental audio file and lights each word as the audio reaches it.
//
// The data-shaping logic is pure + exported so it unit-tests under node:test
// (see player.test.mjs). DOM wiring at the bottom is guarded so importing this
// module in node does not touch `document`.

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
    exportLrc: $("exportLrc"),
    exportJson: $("exportJson"),
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
    vocalGuidePanel: $("vocalGuidePanel"),
    vocalGuideToggle: $("vocalGuideToggle"),
    vocalVolume: $("vocalVolume"),
    vocalVolumeVal: $("vocalVolumeVal"),
    vocalSliderRow: $("vocalSliderRow"),
  };

  let model = { words: [], lines: [] };
  let meta = {};
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
    markLoaded(els.step2, els.jsonDrop, els.jsonName, file);
    document.body.classList.add("has-lyrics");
    setStatus(
      `พร้อมแล้ว! ${model.words.length} คำ · ${model.lines.length} บรรทัด` +
        (payload.aligned ? " · ตรงจังหวะรายคำ" : " · จับเวลาแบบประโยค") +
        `  ·  Ready — ${model.words.length} words`,
      "ok"
    );
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
    const base = (els.apiBase?.value || "http://localhost:8000").trim();
    const progressId =
      (crypto.randomUUID && crypto.randomUUID()) || String(Date.now()) + Math.random();
    if (els.songName) els.songName.textContent = "⏳ " + file.name;
    if (els.songFile) els.songFile.disabled = true;

    const fileMB = (file.size / 1024 / 1024).toFixed(2);
    console.group(`[LyricBridge] runKaraoke — ${file.name}`);
    console.log("file:", file.name, `${fileMB} MB`, file.type || "(no MIME type)");
    console.log("API base:", base);
    console.log("progress ID:", progressId);
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

    // Poll the server's stage every 1.2s and reflect it in the big indicator.
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
      console.time("[LyricBridge] POST /karaoke");
      console.log("→ POST", base.replace(/\/$/, "") + "/karaoke", `(${fileMB} MB upload, starting…)`);
      const payload = await karaokeViaServer(file, base, fetch, progressId);
      console.timeEnd("[LyricBridge] POST /karaoke");
      console.log("← /karaoke OK:", payload);
      clearInterval(poll);
      showStage("fetching");
      // Set audio.src directly — avoids Chrome's ERR_FAILED on fetch().blob() for
      // large (30-50 MB) WAV files. The browser's native audio engine streams it
      // natively and can issue range requests when the user seeks.
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
      // Vocal guide: set src directly (same reason — avoids large fetch.blob())
      if (payload.vocal_url) {
        try {
          loadVocalGuide(base.replace(/\/$/, "") + payload.vocal_url);
        } catch {
          // Vocal load failed — degrade silently (guide panel stays hidden)
        }
      }
    } catch (err) {
      clearInterval(poll);
      const errType = err?.constructor?.name || "Error";
      console.error(`[LyricBridge] /karaoke failed (${errType}):`, err);
      console.log("  err.message :", err.message);
      console.log("  err.stack   :", err.stack);
      console.log("Tip: Network tab → find POST /karaoke → check Status + Response");
      console.groupEnd();
      if (els.songName) els.songName.textContent = "";
      clearProcessing();
      const detail = `[${errType}] ${err.message} — open Console (F12) for full trace`;
      setStatus("ทำคาราโอเกะไม่สำเร็จ · Karaoke failed — " + detail, "error");
    } finally {
      clearInterval(poll);
      if (els.songFile) els.songFile.disabled = false;
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
      const base = (els.apiBase?.value || "http://localhost:8000").trim();
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

  function download(name, text, type) {
    const url = URL.createObjectURL(new Blob([text], { type }));
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  }

  function editWord(w, span) {
    if (!editMode) return;
    // Tap-to-sync: pin this word's start to where the audio is now.
    syncWordStart(model.words, w._i, els.audio.currentTime);
    span.classList.add("edited");
    setStatus(`synced "${w.text}" → ${w.start.toFixed(2)}s`);
  }

  function retypeWord(w, span) {
    if (!editMode) return;
    const next = window.prompt("แก้คำนี้ · Fix this word:", w.text);
    if (next != null && next !== w.text) {
      w.text = next;
      span.textContent = next;
      span.classList.add("edited");
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
    setStatus(`ตอกเวลา "${model.words[i].text}" → ${model.words[i].start.toFixed(2)}s · stamped`, "ok");
  }

  // §5: nudge the active word's start by ±ms (fine alignment of melisma).
  function nudgeActiveWord(deltaMs) {
    const i = lastActive;
    if (i < 0 || !model.words[i]) return;
    syncWordStart(model.words, i, model.words[i].start + deltaMs / 1000);
    wordSpans[i]?.classList.add("edited");
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
}

if (typeof document !== "undefined") {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
}
