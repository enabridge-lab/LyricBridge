# M2 — One-upload flow: เพลงเต็ม → ตัดเสียงร้อง → เล่นบน instrumental + เนื้อร้องซิงก์คำ

> สเปกสำหรับ Claude Code. งานนี้ "ต่อท่อ" ของเดิมให้เป็น flow เดียวจบ ไม่ได้สร้างโมเดลใหม่
> อ่าน `CLAUDE.md` (locked decisions) และ `PRD.md` §7–§8 ก่อนเริ่ม. **ห้ามแก้ locked decisions §2.**

## 1. เป้าหมาย (Goal)

ผู้ใช้อัปโหลด **เพลงเต็มไฟล์เดียว** → ระบบตัดเสียงร้องออก → web player เล่นบน **instrumental** ทันที
พร้อมเนื้อร้องไฮไลต์ทีละคำ (จากการถอดเสียง vocal stem). ปัจจุบันทำได้แต่ต้องอัปโหลดเอง 2 ไฟล์แยก
(instrumental + vocal) และ player ไม่เคยเรียก `/separate` เลย.

User uploads ONE full song → vocals removed → player plays the instrumental immediately,
with word-synced Thai lyrics transcribed from the vocal stem. Today this requires the user to
manually supply two separate stems; `player.js` never calls `/separate`.

## 2. ของเดิมที่มีแล้ว (Reuse — อย่าเขียนใหม่)

- `server/app/separate.py` → `separate(input_path, work_dir)` คืน `SeparationResult(vocals_path, instrumental_path, model, device)`. ทำงานจริงแล้ว (verified บนเพลงไทย ~262s).
- `server/app/main.py` → `POST /separate` (เพลง → zip ของ vocals.wav+instrumental.wav), `POST /transcribe` (vocal → words/lrc/ass JSON), `_inference_lock`, `_save_upload` (cap `MAX_UPLOAD_MB`).
- `web/player.js` → `loadAudio(file)` ตั้ง `els.audio.src`; `transcribeViaServer(file, apiBase)` POST `/transcribe`; `buildModel()`/`activeWordIndex()` ไฮไลต์คำ (อิง `word.start` → immune ต่อบั๊ก ASS `\k`).

## 3. ปัญหาที่ต้องตัดสินใจ (Key constraint)

Separation บน CPU ใช้เวลาหลายนาที (เพลงทดสอบ ~22 นาที). **ห้ามแยกเสียงสองรอบ.** ดังนั้น flow
ที่เรียก `/separate` แล้วตามด้วย `/transcribe` แยกกัน = แยกเสียงรอบเดียวพอ แต่ instrumental
ก้อนใหญ่ต้องเดินทางกลับ browser. `/separate` เดิมคืนเป็น **zip** ซึ่ง browser ต้องแตกเอง.

`_inference_lock` serialize ทั้ง `/separate` และ `/transcribe` อยู่แล้ว → ห้ามถือ lock ค้างข้าม request.

## 4. การออกแบบที่แนะนำ (Recommended design) — endpoint `/karaoke` รอบเดียว

เพิ่ม endpoint เดียวที่ทำ separation **ครั้งเดียว** แล้ว transcribe ต่อจาก vocal stem ใน request เดียวกัน:

```
POST /karaoke   (multipart: file=<เพลงเต็ม>, lang=th)
  1. _save_upload(file)               # cap MAX_UPLOAD_MB เหมือนเดิม → 413 ถ้าเกิน
  2. with _inference_lock:
        result = separate.separate(input_path, tmpdir)     # 1 รอบเท่านั้น
        segments+words = <pipeline เดิมจาก _transcribe_locked> บน result.vocals_path
  3. ย้าย result.instrumental_path → temp store ที่มี job_id (TTL ~10 นาที)
  4. คืน JSON: { job_id, instrumental_url: "/instrumental/<job_id>",
                 language, duration_sec, words, lrc, ass, aligned }

GET /instrumental/<job_id>
  - stream ไฟล์ instrumental.wav ออกไป (FileResponse) แล้ว "ลบทิ้งหลังส่งจบ" ผ่าน BackgroundTask
  - 404 ถ้า job_id หมดอายุ/ไม่พบ
```

**Refactor ที่ต้องทำใน main.py:** ดึงแกน pipeline ใน `_transcribe_locked` (stage [3] tokenize + [4]
build LRC/ASS) ออกมาเป็นฟังก์ชันที่รับ `segments`/`char_map` เพื่อให้ `/karaoke` เรียกใช้ซ้ำได้
โดยไม่ก๊อปโค้ด. รักษาวินัยเดิม: `asr.free_model()` / `align.free_model()` ระหว่าง stage
(PRD §5.1 — Demucs กับ Whisper ห้ามอยู่บน GPU พร้อมกัน).

**เรื่อง state/TTL:** working agreement บอก "stateless, don't persist user audio". instrumental เป็น
ไฟล์ชั่วคราวที่ลบทันทีหลัง stream + มี TTL sweeper → ยังถือว่าไม่ persist ถาวร. เก็บใน temp dir
มี job_id เป็น opaque token, ห้ามเก็บ vocal stem ต้นฉบับ, ลบ tmpdir ที่เหลือทั้งหมด.

## 5. ฝั่ง browser (`web/player.js` + `web/index.html`)

1. เพิ่มฟังก์ชัน export ใหม่ (unit-testable, รับ `fetchImpl` injectable เหมือน `transcribeViaServer`):
   ```js
   export async function karaokeViaServer(file, apiBase, fetchImpl = fetch) {
     // POST /karaoke → JSON {job_id, instrumental_url, words, lrc, ass, ...}
     // โยน Error พร้อม stage/error ของ server เมื่อ !res.ok (ทำแบบเดียวกับ transcribeViaServer)
   }
   ```
2. เพิ่มช่องอัปโหลด "เพลงเต็ม" (Step 0) ใน `index.html` + dropzone (ใช้ `wireDrop` เดิม).
3. เมื่อได้ผลจาก `/karaoke`:
   - `fetch(apiBase + payload.instrumental_url)` → `blob()` → `loadAudio(new File([blob],"instrumental.wav"))`
     (หรือ `els.audio.src = URL.createObjectURL(blob)` ตรงๆ).
   - `buildModel(payload)` → render เนื้อร้องเหมือน flow `/transcribe` เดิม.
4. **Progress UI:** separation นานหลายนาที — ต้องมีสถานะ "กำลังตัดเสียงร้อง…" (disable ปุ่ม, spinner).
   ตั้ง `fetch` ไม่มี timeout สั้น. แสดง error ที่ server ส่งมา (stage/message) ถ้าล้มเหลว.
5. flow เดิม (อัปโหลด instrumental + vocal แยก) **คงไว้** เป็นโหมด manual/ขั้นสูง — ไม่ลบทิ้ง.

## 6. ทางเลือกอื่น (Alternatives ที่พิจารณาแล้ว)

- **A. เพิ่ม endpoint คืน instrumental ไฟล์เดียว (ไม่ใช่ zip)** — ง่ายแต่ยัง 2 request แยก แปลว่า
  separation อาจรันซ้ำถ้าทำ transcribe คนละรอบ → แพงบน CPU. ใช้ได้ถ้ายอม cache ผลแยกเสียงด้วย job_id.
- **B. ให้ browser แตก zip จาก `/separate` เดิม** — ไม่แตะ server แต่ต้องเพิ่มไลบรารี unzip ใน browser
  และยังต้อง transcribe อีกรอบ (separation ซ้ำ). ไม่แนะนำ.
- **เลือก §4 (`/karaoke` รอบเดียว)** เพราะแยกเสียงครั้งเดียว, payload เนื้อร้องเป็น JSON เบาๆ, ไฟล์
  instrumental ดึงทีหลังแบบ stream แล้วลบ.

## 7. Acceptance criteria

- [ ] อัปโหลดเพลงเต็ม **ไฟล์เดียว** แล้วเล่นบน instrumental ได้ พร้อมเนื้อร้องไฮไลต์ทีละคำ โดยไม่ต้องอัปโหลดไฟล์ที่สองเอง.
- [ ] Separation รัน **ครั้งเดียว** ต่อเพลง (ตรวจ log / ไม่เรียก `separate.separate` ซ้ำ).
- [ ] อัปโหลดเกิน `MAX_UPLOAD_MB` → 413 เหมือนเดิม.
- [ ] instrumental ถูกลบหลัง stream จบ และ job หมดอายุตาม TTL (ไม่มีไฟล์ค้าง /tmp).
- [ ] `asr.free_model()`/`align.free_model()` ยังถูกเรียกระหว่าง stage (GPU ไม่ co-resident).
- [ ] โหมด manual เดิม (instrumental+vocal แยก) ยังทำงาน.
- [ ] เพิ่มเทสต์: contract test `/karaoke` (monkeypatch `separate.separate` + pipeline ให้ไม่โหลดโมเดลจริง, แบบเดียวกับ `server/tests/test_api.py`); unit test `karaokeViaServer` ด้วย fake fetch.
- [ ] `device cpu` path ทำงาน (PRD §5 — self-host ไม่มี GPU).

## 8. ลำดับงานแนะนำ (Build order)

1. Refactor `main.py`: แยกแกน pipeline ([3]+[4]) ออกจาก `_transcribe_locked` ให้ reuse ได้.
2. เพิ่ม `POST /karaoke` + `GET /instrumental/<job_id>` + temp store + TTL sweeper.
3. เทสต์ server (contract, monkeypatched) — ต้องผ่านก่อนแตะ browser.
4. `player.js`: `karaokeViaServer` + wiring + progress UI; `index.html` Step 0.
5. เทสต์ browser unit (`web/player.test.mjs`) สำหรับ `karaokeViaServer`.
6. Smoke ปลายทางด้วยเพลงจริงสั้นๆ (ตัด `-t` หรือเพลง ~30s) แล้ว verify เล่น instrumental + เห็นคำวิ่ง.
```
