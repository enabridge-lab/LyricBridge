// Pure-logic tests for the M2 player. Run: node --test  (from web/)
// No DOM, no deps — exercises the timing/grouping functions the highlight relies on.

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  parseLrc,
  groupWordsIntoLines,
  activeWordIndex,
  buildModel,
  serializeLrc,
  serializeWords,
  serializeLines,
  serializePayload,
  renderVideoViaServer,
  buildRenderStyle,
  syncWordStart,
  transcribeViaServer,
  karaokeViaServer,
  withOffset,
  stageLabel,
  LOW_CONF,
  isLowConfidence,
  syncQuality,
  submitKaraokeJob,
  pollKaraokeJob,
  saveJobRef,
  loadJobRef,
  clearJobRef,
  defaultApiBase,
  wordProgress,
  verseCountdown,
  clampPlaybackRate,
  loopedTime,
  sanitizePlayerTheme,
  preferredRecorderMime,
  cleanWordText,
  wantsDemo,
} from "./player.js";

// D4: backend URL resolution — meta override for hosted build, localhost for self-host.
test("defaultApiBase falls back to localhost when no/empty meta", () => {
  assert.equal(defaultApiBase(null), "http://localhost:8000");
  const emptyDoc = { querySelector: () => ({ getAttribute: () => "" }) };
  assert.equal(defaultApiBase(emptyDoc), "http://localhost:8000");
  const missingDoc = { querySelector: () => null };
  assert.equal(defaultApiBase(missingDoc), "http://localhost:8000");
});

test("defaultApiBase uses the meta content (trimmed) when set", () => {
  const doc = { querySelector: () => ({ getAttribute: () => "  https://x--lyricbridge-web.modal.run  " }) };
  assert.equal(defaultApiBase(doc), "https://x--lyricbridge-web.modal.run");
});

// Map-backed stand-in for localStorage (node has no DOM storage).
function fakeStorage() {
  const m = new Map();
  return {
    setItem: (k, v) => m.set(k, String(v)),
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    removeItem: (k) => m.delete(k),
  };
}

function lines(...lineWordArrays) {
  return lineWordArrays.map((ws) => ({ words: ws }));
}

test("parseLrc reads mm:ss.xx timestamps and skips junk lines", () => {
  const lrc = "[00:04.91]เมื่อวันที่\n[01:26.11]ต้องเป็นไป\nnot a tag\n[02:01.10]จง";
  const lines = parseLrc(lrc);
  assert.equal(lines.length, 3);
  assert.ok(Math.abs(lines[0].start - 4.91) < 1e-9);
  assert.ok(Math.abs(lines[1].start - 86.11) < 1e-9); // 1*60 + 26.11
  assert.equal(lines[2].text, "จง");
});

test("groupWordsIntoLines buckets words by line start boundaries", () => {
  const words = [
    { text: "a", start: 0.0, end: 0.5 },
    { text: "b", start: 0.6, end: 1.0 },
    { text: "c", start: 5.0, end: 5.4 }, // belongs to the second line
    { text: "d", start: 5.5, end: 6.0 },
  ];
  const lines = groupWordsIntoLines(words, [0.0, 5.0]);
  assert.equal(lines.length, 2);
  assert.deepEqual(lines[0].words.map((w) => w.text), ["a", "b"]);
  assert.deepEqual(lines[1].words.map((w) => w.text), ["c", "d"]);
});

test("groupWordsIntoLines falls back to one line when no LRC starts", () => {
  const words = [{ text: "x", start: 1, end: 2 }];
  const lines = groupWordsIntoLines(words, []);
  assert.equal(lines.length, 1);
  assert.equal(lines[0].words.length, 1);
});

test("groupWordsIntoLines drops empty lines from duplicate timestamps", () => {
  const words = [{ text: "a", start: 0, end: 1 }, { text: "b", start: 0, end: 1 }];
  // two identical line starts -> second bucket gets nothing -> dropped
  const lines = groupWordsIntoLines(words, [0.0, 0.0]);
  assert.equal(lines.length, 1);
  assert.equal(lines[0].words.length, 2);
});

test("activeWordIndex returns the last word started at-or-before t", () => {
  const words = [
    { text: "a", start: 0.0, end: 1.0 },
    { text: "b", start: 1.0, end: 2.0 },
    { text: "c", start: 2.0, end: 3.0 },
  ];
  assert.equal(activeWordIndex(words, -0.1), -1); // before the song
  assert.equal(activeWordIndex(words, 0.0), 0);
  assert.equal(activeWordIndex(words, 1.5), 1);
  assert.equal(activeWordIndex(words, 2.0), 2);   // boundary -> the newer word
  assert.equal(activeWordIndex(words, 99), 2);    // after the end -> stays on last
});

test("serializeLrc round-trips edited lines back to [mm:ss.xx]text", () => {
  const ls = lines(
    [{ text: "ก", start: 4.91, end: 5.0 }, { text: "ข", start: 5.0, end: 5.5 }],
    [{ text: "ค", start: 86.11, end: 86.5 }]
  );
  const lrc = serializeLrc(ls);
  assert.equal(lrc, "[00:04.91]กข\n[01:26.11]ค");
  // and it parses back to the same line starts
  assert.deepEqual(parseLrc(lrc).map((l) => l.start), [4.91, 86.11]);
});

test("serializeWords flattens lines to text/start/end only", () => {
  const ls = lines([{ text: "ก", start: 1, end: 2, _i: 0, extra: "x" }]);
  assert.deepEqual(serializeWords(ls), [{ text: "ก", start: 1, end: 2 }]);
});

test("serializePayload produces a /transcribe-shaped, re-ingestible object", () => {
  const ls = lines([{ text: "ก", start: 0, end: 1 }, { text: "ข", start: 1, end: 2 }]);
  const p = serializePayload(ls, { language: "th", duration_sec: 5 });
  assert.equal(p.language, "th");
  assert.equal(p.duration_sec, 5);
  assert.equal(p.aligned, true);
  assert.equal(p.edited, true);
  assert.equal(p.words.length, 2);
  // feeding it back through buildModel works (round-trip)
  const m = buildModel(p);
  assert.equal(m.words.length, 2);
});

test("serializeLines keeps line structure and strips words to text/start/end", () => {
  const ls = lines(
    [{ text: "ก", start: 0, end: 1, _i: 0, extra: "x" }, { text: "ข", start: 1, end: 2, _i: 1 }],
    [],                                              // empty line dropped
    [{ text: "ค", start: 5, end: 6, _i: 2 }]
  );
  assert.deepEqual(serializeLines(ls), [
    [{ text: "ก", start: 0, end: 1 }, { text: "ข", start: 1, end: 2 }],
    [{ text: "ค", start: 5, end: 6 }],
  ]);
});

test("serializeLines round-trips with serializeWords (flatten equals flat)", () => {
  const ls = lines(
    [{ text: "ก", start: 0, end: 1 }, { text: "ข", start: 1, end: 2 }],
    [{ text: "ค", start: 5, end: 6 }]
  );
  assert.deepEqual(serializeLines(ls).flat(), serializeWords(ls));
  // and the payload exporter agrees on the same word stream
  assert.deepEqual(serializePayload(ls).words, serializeLines(ls).flat());
});

test("renderVideoViaServer posts JSON lines to /render/{job_id} and returns a blob", async () => {
  let seenUrl, seenMethod, seenBody, seenType;
  const fakeBlob = { fake: "blob" };
  const fakeFetch = async (url, opts) => {
    seenUrl = url;
    seenMethod = opts.method;
    seenType = opts.headers["Content-Type"];
    seenBody = JSON.parse(opts.body);
    return { ok: true, blob: async () => fakeBlob };
  };
  const ls = [[{ text: "ก", start: 0, end: 1 }]];
  const out = await renderVideoViaServer("job42", ls, "http://localhost:8000/", fakeFetch);
  assert.equal(seenUrl, "http://localhost:8000/render/job42"); // trailing slash trimmed
  assert.equal(seenMethod, "POST");
  assert.equal(seenType, "application/json");
  assert.deepEqual(seenBody, { lines: ls });
  assert.equal(out, fakeBlob);
});

test("buildRenderStyle normalizes UI values and drops junk", () => {
  assert.deepEqual(
    buildRenderStyle({
      font: "Noto Sans Thai",
      fontSize: "64",
      primary: "#ffffff",     // colour input gives #rrggbb
      highlight: "FFA500",    // already bare hex
      alignment: "8",
    }),
    { font: "Noto Sans Thai", font_size: 64, primary_colour: "FFFFFF",
      highlight_colour: "FFA500", alignment: 8 }
  );
  // junk in -> dropped (server defaults apply), never throws
  assert.deepEqual(
    buildRenderStyle({ fontSize: "huge", primary: "red", alignment: "" }),
    {}
  );
  assert.deepEqual(buildRenderStyle(), {});
});

test("renderVideoViaServer merges style fields into the JSON body", async () => {
  let seenBody;
  const fakeFetch = async (url, opts) => {
    seenBody = JSON.parse(opts.body);
    return { ok: true, blob: async () => ({}) };
  };
  const ls = [[{ text: "ก", start: 0, end: 1 }]];
  await renderVideoViaServer("j1", ls, "http://localhost:8000", fakeFetch,
    { font: "Sarabun", font_size: 64 });
  assert.deepEqual(seenBody, { lines: ls, font: "Sarabun", font_size: 64 });
  // and without a style the body stays lines-only (back-compat)
  await renderVideoViaServer("j1", ls, "http://localhost:8000", fakeFetch);
  assert.deepEqual(seenBody, { lines: ls });
});

test("renderVideoViaServer surfaces the server's stage/error on failure", async () => {
  const fakeFetch = async () => ({
    ok: false,
    status: 404,
    json: async () => ({ error: "job not found or expired", stage: "render" }),
  });
  await assert.rejects(
    () => renderVideoViaServer("gone", [], "http://localhost:8000", fakeFetch),
    /render: job not found or expired/
  );
});

test("syncWordStart pins a start and keeps the line non-overlapping", () => {
  const words = [
    { text: "a", start: 0, end: 1 },
    { text: "b", start: 1, end: 2 },
  ];
  syncWordStart(words, 1, 0.5);          // move b earlier, into a's span
  assert.equal(words[1].start, 0.5);
  assert.equal(words[0].end, 0.5);        // previous word's end pulled back
  syncWordStart(words, 1, 3.0);          // move b past its own end
  assert.equal(words[1].start, 3.0);
  assert.equal(words[1].end, 3.0);        // end clamped up to start
});

test("transcribeViaServer posts to /transcribe and returns parsed payload", async () => {
  let seenUrl, seenMethod, seenHasFile;
  const fakeFetch = async (url, opts) => {
    seenUrl = url;
    seenMethod = opts.method;
    seenHasFile = opts.body instanceof FormData && opts.body.has("file");
    return { ok: true, json: async () => ({ words: [{ text: "ก", start: 0, end: 1 }], lrc: "" }) };
  };
  const out = await transcribeViaServer(new Blob(["x"]), "http://localhost:8000/", fakeFetch);
  assert.equal(seenUrl, "http://localhost:8000/transcribe"); // trailing slash trimmed
  assert.equal(seenMethod, "POST");
  assert.equal(seenHasFile, true);
  assert.equal(out.words.length, 1);
});

test("transcribeViaServer surfaces the server's stage/error on failure", async () => {
  const fakeFetch = async () => ({
    ok: false,
    status: 422,
    json: async () => ({ error: "no speech detected", stage: "asr" }),
  });
  await assert.rejects(
    () => transcribeViaServer(new Blob(["x"]), "http://localhost:8000", fakeFetch),
    /asr: no speech detected/
  );
});

test("karaokeViaServer posts a full song to /karaoke and returns job payload", async () => {
  let seenUrl, seenMethod, seenHasFile;
  const fakeFetch = async (url, opts) => {
    seenUrl = url;
    seenMethod = opts.method;
    seenHasFile = opts.body instanceof FormData && opts.body.has("file");
    return {
      ok: true,
      json: async () => ({
        job_id: "abc123",
        instrumental_url: "/instrumental/abc123",
        words: [{ text: "ก", start: 0, end: 1 }],
        lrc: "[00:00.00]ก",
      }),
    };
  };
  const out = await karaokeViaServer(new Blob(["song"]), "http://localhost:8000/", fakeFetch);
  assert.equal(seenUrl, "http://localhost:8000/karaoke"); // trailing slash trimmed
  assert.equal(seenMethod, "POST");
  assert.equal(seenHasFile, true);
  assert.equal(out.job_id, "abc123");
  assert.equal(out.instrumental_url, "/instrumental/abc123");
});

test("karaokeViaServer surfaces the server's stage/error on failure", async () => {
  const fakeFetch = async () => ({
    ok: false,
    status: 500,
    json: async () => ({ error: "Demucs failed", stage: "separate" }),
  });
  await assert.rejects(
    () => karaokeViaServer(new Blob(["x"]), "http://localhost:8000", fakeFetch),
    /separate: Demucs failed/
  );
});

test("withOffset shifts the comparison time, and activeWordIndex respects it", () => {
  const words = [
    { text: "a", start: 0.0, end: 1.0 },
    { text: "b", start: 1.0, end: 2.0 },
    { text: "c", start: 2.0, end: 3.0 },
  ];
  // no offset: at t=1.2 -> word "b" (index 1)
  assert.equal(activeWordIndex(words, withOffset(1.2, 0)), 1);
  // +300ms: lyrics lead -> effective 1.5 still "b"; at 0.8+300ms=1.1 -> "b" not "a"
  assert.equal(activeWordIndex(words, withOffset(0.8, 300)), 1);
  // -300ms: lyrics lag -> effective 0.9 -> "a" (index 0)
  assert.equal(activeWordIndex(words, withOffset(1.2, -300)), 0);
  // non-numeric offset treated as 0
  assert.equal(withOffset(2.0, undefined), 2.0);
  assert.equal(withOffset(2.0, ""), 2.0);
});

test("stageLabel maps each pipeline stage to a step number + bilingual text", () => {
  assert.equal(stageLabel("separating").step, 1);
  assert.equal(stageLabel("transcribing").step, 2);
  assert.equal(stageLabel("aligning").step, 3);
  assert.equal(stageLabel("building").step, 4);
  assert.ok(stageLabel("separating").th && stageLabel("separating").en);
  // unknown stage falls back gracefully
  assert.equal(stageLabel("???").step, 0);
});

test("karaokeViaServer forwards the progress_id to the server", async () => {
  let hasPid = false;
  const fakeFetch = async (url, opts) => {
    hasPid = opts.body instanceof FormData && opts.body.get("progress_id") === "pid-123";
    return { ok: true, json: async () => ({ job_id: "j", instrumental_url: "/instrumental/j", words: [], lrc: "" }) };
  };
  await karaokeViaServer(new Blob(["s"]), "http://localhost:8000", fakeFetch, "pid-123");
  assert.equal(hasPid, true);
});

test("syncQuality grades payloads across all branches and thresholds", () => {
  // no sync info at all (pre-sync-era JSON) -> null = show no badge
  assert.equal(syncQuality({}), null);
  assert.equal(syncQuality(null), null);
  // aligned false -> rough, no pct
  assert.deepEqual(syncQuality({ aligned: false }), { level: "rough", pct: null });
  // aligned true but no counts (e.g. hand-edited export) -> good, no pct
  assert.deepEqual(syncQuality({ aligned: true }), { level: "good", pct: null });
  assert.deepEqual(
    syncQuality({ aligned: true, total_segment_count: 0 }),
    { level: "good", pct: null }                      // total=0 guarded
  );
  // threshold edges: pct >= 80 good, >= 40 partial, below rough
  const q = (deg, tot) =>
    syncQuality({ aligned: true, degraded_segment_count: deg, total_segment_count: tot });
  assert.deepEqual(q(0, 10), { level: "good", pct: 100 });
  assert.deepEqual(q(2, 10), { level: "good", pct: 80 });      // exactly 80 -> good
  assert.deepEqual(q(3, 10), { level: "partial", pct: 70 });
  assert.deepEqual(q(6, 10), { level: "partial", pct: 40 });   // exactly 40 -> partial
  assert.deepEqual(q(7, 10), { level: "rough", pct: 30 });
});

test("syncQuality grades a real eval payload from out_vocals_fixed", async () => {
  const { readFile, readdir } = await import("node:fs/promises");
  const dir = new URL("../server/tests/out_vocals_fixed/", import.meta.url);
  let jsons;
  try {
    jsons = (await readdir(dir)).filter((f) => f.endsWith(".json"));
  } catch (e) {
    if (e.code === "ENOENT") return; // eval outputs dir absent in this checkout (e.g. CI)
    throw e;
  }
  if (!jsons.length) return; // eval outputs present but empty
  const payload = JSON.parse(await readFile(new URL(jsons[0], dir), "utf8"));
  const quality = syncQuality(payload);
  assert.ok(quality === null || ["good", "partial", "rough"].includes(quality.level));
  // these known eval files are aligned:true -> must NOT come out "rough"
  if (payload.aligned === true) assert.notEqual(quality.level, "rough");
});

test("submitKaraokeJob posts the song and returns the job ref", async () => {
  let seenUrl, seenHasFile, seenLang;
  const fakeFetch = async (url, opts) => {
    seenUrl = url;
    seenHasFile = opts.body instanceof FormData && opts.body.has("file");
    seenLang = opts.body.get("lang");
    return { ok: true, status: 202, json: async () => ({ job_id: "j1", status_url: "/jobs/j1" }) };
  };
  const out = await submitKaraokeJob(new Blob(["song"]), "http://localhost:8000/", fakeFetch);
  assert.equal(seenUrl, "http://localhost:8000/jobs/karaoke");
  assert.equal(seenHasFile, true);
  assert.equal(seenLang, "th");
  assert.equal(out.job_id, "j1");
});

test("submitKaraokeJob returns null on 404 (old server -> legacy fallback)", async () => {
  const fakeFetch = async () => ({ ok: false, status: 404, json: async () => ({}) });
  assert.equal(await submitKaraokeJob(new Blob(["x"]), "http://localhost:8000", fakeFetch), null);
});

test("submitKaraokeJob surfaces a full queue (429) as stage error", async () => {
  const fakeFetch = async () => ({
    ok: false,
    status: 429,
    json: async () => ({ error: "queue full (3 jobs waiting); try again later", stage: "queue" }),
  });
  await assert.rejects(
    () => submitKaraokeJob(new Blob(["x"]), "http://localhost:8000", fakeFetch),
    /queue: queue full/
  );
});

test("pollKaraokeJob loops until done, reporting each status, then stops", async () => {
  const statuses = [
    { status: "queued", queue_position: 2, stage: "queued", step: 0 },
    { status: "running", stage: "separating", step: 1 },
    { status: "done", stage: "done", step: 4, result: { words: [], lrc: "", job_id: "j1" } },
  ];
  let calls = 0;
  const seen = [];
  const fakeFetch = async () => ({ ok: true, json: async () => statuses[calls++] });
  const result = await pollKaraokeJob("j1", "http://localhost:8000/", {
    fetchImpl: fakeFetch,
    sleep: async () => {},                  // no real timers in tests
    onUpdate: (st) => seen.push(st.status),
  });
  assert.equal(calls, 3);                   // stopped exactly at "done"
  assert.deepEqual(seen, ["queued", "running", "done"]);
  assert.equal(result.job_id, "j1");
});

test("pollKaraokeJob throws the job's stage/error when it failed", async () => {
  const fakeFetch = async () => ({
    ok: true,
    json: async () => ({ status: "error", error: { error: "Demucs exploded", stage: "separate" } }),
  });
  await assert.rejects(
    () => pollKaraokeJob("j9", "http://localhost:8000", { fetchImpl: fakeFetch, sleep: async () => {} }),
    /separate: Demucs exploded/
  );
});

test("job ref round-trips through storage and survives junk", () => {
  const s = fakeStorage();
  assert.equal(loadJobRef(s), null);                      // empty -> null
  saveJobRef(s, "job42", "http://localhost:8000");
  assert.deepEqual(loadJobRef(s), { jobId: "job42", base: "http://localhost:8000" });
  clearJobRef(s);
  assert.equal(loadJobRef(s), null);                      // cleared
  s.setItem("lyricbridgeJob", "{not json");
  assert.equal(loadJobRef(s), null);                      // junk -> null, no throw
});

test("isLowConfidence flags only known-low scores (old payloads never flag)", () => {
  assert.equal(isLowConfidence({ confidence: LOW_CONF - 0.1 }), true);
  assert.equal(isLowConfidence({ confidence: LOW_CONF }), false);      // at threshold = ok
  assert.equal(isLowConfidence({ confidence: 0.9 }), false);
  assert.equal(isLowConfidence({}), false);                            // pre-F3 payload
  assert.equal(isLowConfidence({ confidence: null }), false);          // unknown ≠ bad
  assert.equal(isLowConfidence(undefined), false);
});

test("buildModel keeps word confidence through to the view-model", () => {
  const payload = {
    lrc: "[00:00.00]ก",
    words: [
      { text: "ก", start: 0, end: 1, confidence: 0.3 },
      { text: "ข", start: 1, end: 2 }, // old-style word without the field
    ],
  };
  const m = buildModel(payload);
  assert.equal(m.words[0].confidence, 0.3);
  assert.equal(m.words[1].confidence, undefined);
});

test("serializeWords drops runtime hints (confidence/interpolated/roman) from exports", () => {
  // F3/F6/F7: an edited export is "confirmed" — none of the hint fields leave.
  const ls = lines([
    { text: "ก", start: 1, end: 2, confidence: 0.4, interpolated: true, roman: "ko", _i: 0 },
  ]);
  assert.deepEqual(serializeWords(ls), [{ text: "ก", start: 1, end: 2 }]);
  assert.deepEqual(serializeLines(ls), [[{ text: "ก", start: 1, end: 2 }]]);
});

test("buildModel passes interpolated and roman through to the view-model", () => {
  const payload = {
    lrc: "[00:00.00]ก",
    words: [
      { text: "ก", start: 0, end: 1, interpolated: true, roman: "ko" },
      { text: "ข", start: 1, end: 2 }, // pre-F6/F7 word -> no flags, no error
    ],
  };
  const m = buildModel(payload);
  assert.equal(m.words[0].interpolated, true);
  assert.equal(m.words[0].roman, "ko");
  assert.equal(m.words[1].interpolated, undefined);
});

test("buildModel assigns a contiguous flat index across all line words", () => {
  const payload = {
    lrc: "[00:00.00]a\n[00:05.00]b",
    words: [
      { text: "a", start: 0.0, end: 0.5 },
      { text: "b", start: 5.0, end: 5.5 },
    ],
  };
  const m = buildModel(payload);
  assert.equal(m.lines.length, 2);
  assert.equal(m.lines[0].words[0]._i, 0);
  assert.equal(m.lines[1].words[0]._i, 1);
});

// ── Phase S: sing-mode pure helpers ────────────────────────────────────────
test("wordProgress clamps 0..1 and survives tiny/zero durations", () => {
  const w = { start: 10, end: 12 };
  assert.equal(wordProgress(w, 9), 0);      // before
  assert.equal(wordProgress(w, 11), 0.5);   // halfway
  assert.equal(wordProgress(w, 99), 1);     // after -> clamped
  assert.equal(wordProgress({ start: 5, end: 5 }, 5), 0); // zero dur -> no NaN
  assert.equal(wordProgress(null, 1), 0);
});

test("verseCountdown shows 3-2-1 only before a post-silence verse entrance", () => {
  // word at t=10 follows a long silence (prevEnd 0) -> verse entrance
  const words = [{ start: 10, end: 11 }, { start: 11, end: 12 }];
  assert.equal(verseCountdown(words, 7.2), 3);   // 2.8s away -> ceil 3
  assert.equal(verseCountdown(words, 8.5), 2);   // 1.5s away
  assert.equal(verseCountdown(words, 9.5), 1);   // 0.5s away
  assert.equal(verseCountdown(words, 5), null);  // >lead (3s) away
  assert.equal(verseCountdown(words, 10.5), null); // already past the entrance
  // a word with only a small gap before it is NOT a verse entrance
  const tight = [{ start: 0, end: 1 }, { start: 1.2, end: 2 }];
  assert.equal(verseCountdown(tight, 0.5), null);
  assert.equal(verseCountdown([], 1), null);
});

test("clampPlaybackRate keeps 0.5..1.5 and defaults junk to 1", () => {
  assert.equal(clampPlaybackRate(1), 1);
  assert.equal(clampPlaybackRate(0.5), 0.5);
  assert.equal(clampPlaybackRate(0.1), 0.5);   // floor
  assert.equal(clampPlaybackRate(3), 1.5);     // ceil
  assert.equal(clampPlaybackRate("0.75"), 0.75);
  assert.equal(clampPlaybackRate("fast"), 1);  // NaN -> 1
});

test("loopedTime jumps back to A only past B, ignores invalid regions", () => {
  assert.equal(loopedTime(5, 2, 8), 5);    // inside region -> unchanged
  assert.equal(loopedTime(9, 2, 8), 2);    // past B -> back to A
  assert.equal(loopedTime(9, null, 8), 9); // no A -> off
  assert.equal(loopedTime(9, 2, null), 9); // no B -> off
  assert.equal(loopedTime(9, 8, 2), 9);    // B<=A invalid -> ignored
});

test("sanitizePlayerTheme validates size + hex colours, never throws", () => {
  assert.deepEqual(sanitizePlayerTheme({ size: "lg", text: "#112233", bg: "#ffffff" }),
    { size: "lg", text: "#112233", bg: "#ffffff" });
  assert.deepEqual(sanitizePlayerTheme({ size: "huge", text: "red", bg: "#GGG" }),
    { size: "md", text: null, bg: null }); // unknown size -> md; bad colours -> null
  assert.deepEqual(sanitizePlayerTheme(null), { size: "md", text: null, bg: null });
  assert.deepEqual(sanitizePlayerTheme("x"), { size: "md", text: null, bg: null });
});

test("preferredRecorderMime picks the first supported, else empty string", () => {
  assert.equal(preferredRecorderMime((m) => m === "audio/webm"), "audio/webm");
  assert.equal(preferredRecorderMime(() => false), ""); // none supported -> browser default
  assert.equal(preferredRecorderMime(undefined), "");   // no API -> ""
  assert.equal(preferredRecorderMime((m) => m === "audio/mp4",
    ["audio/ogg", "audio/mp4"]), "audio/mp4");           // custom prefs
});

// ── Phase E1: inline-edit text sanitizer ───────────────────────────────────
test("cleanWordText collapses contentEditable whitespace and trims", () => {
  assert.equal(cleanWordText("  ฉัน  "), "ฉัน");
  assert.equal(cleanWordText("ฉัน\nรัก"), "ฉัน รัก");     // stray newline -> single space
  assert.equal(cleanWordText("a  b"), "a  b".replace(/\s+/g, " ")); // nbsp+space
  assert.equal(cleanWordText(""), "");
  assert.equal(cleanWordText(null), "");
  assert.equal(cleanWordText(undefined), "");
});

// D1: ?demo=1 detection (drives auto-loading the pre-baked, no-backend demo).
test("wantsDemo is true only for demo=1 and never throws on junk", () => {
  assert.equal(wantsDemo("?demo=1"), true);
  assert.equal(wantsDemo("demo=1"), true);          // leading ? optional
  assert.equal(wantsDemo("?foo=bar&demo=1"), true); // among other params
  assert.equal(wantsDemo("?demo=0"), false);
  assert.equal(wantsDemo("?demo=true"), false);     // strictly "1"
  assert.equal(wantsDemo(""), false);
  assert.equal(wantsDemo(), false);                 // default arg
  assert.equal(wantsDemo("%%%not-a-query%%%"), false);
});
