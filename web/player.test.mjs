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
  serializePayload,
  syncWordStart,
  transcribeViaServer,
  karaokeViaServer,
  withOffset,
  stageLabel,
} from "./player.js";

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
