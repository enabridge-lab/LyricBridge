# SPEC — ฟีเจอร์ถัดไป 2 ตัว (ให้ Claude Code อ่านก่อนเริ่มงาน)

> อ่าน `CLAUDE.md` และ `docs/HANDOFF.md` ก่อน. ห้ามแตะ locked decisions (PRD §2).
> ทำทีละฟีเจอร์ ตามลำดับ F1 → F2. ทุกข้อต้องมี CPU path ที่ใช้ได้ และ test ต้องผ่านทั้งหมด.

---

## F1 — บีบอัด stem เป็นไฟล์เล็กก่อนเสิร์ฟ (แก้ต้นตอ WAV 35 MB)

### ปัญหาปัจจุบัน

- `_store_instrumental` / `_store_vocal` (ใน `server/app/main.py`) เก็บ WAV ดิบ ~35-50 MB
- `GET /instrumental/{job_id}` และ `GET /vocal/{job_id}` ใช้ `path.read_bytes()` อ่านทั้งไฟล์เข้า RAM
  ต่อ request — กิน RAM, ไม่รองรับ range request (seek = โหลดใหม่ทั้งก้อน),
  และเป็นต้นตอของ Chrome ERR_FAILED ที่เคย workaround ไปแล้ว

### สิ่งที่ต้องทำ

1. **เพิ่มฟังก์ชัน encode** (ไฟล์ใหม่หรือใน `render.py` ก็ได้ เพราะใช้ ffmpeg เหมือนกัน):
   ```
   def encode_stem(src_wav: Path, dest: Path, bitrate: str = STEM_BITRATE) -> Path
   ```
   - ใช้ ffmpeg ที่มีอยู่แล้ว (M3 ใช้อยู่): `ffmpeg -i src.wav -c:a aac -b:a 128k out.m4a`
   - **เลือก M4A/AAC เป็น default** (Safari + ทุก browser เล่นได้ ต่างจาก Opus)
   - bitrate ตั้งผ่าน env `STEM_BITRATE` (default `128k`), ปิดฟีเจอร์ได้ด้วย
     `STEM_ENCODE=0` → fallback เก็บ WAV เหมือนเดิม (กันกรณี ffmpeg/aac ใช้ไม่ได้)
   - encode ล้มเหลว → log WARNING แล้ว fallback WAV (degrade gracefully ตามธรรมเนียม repo)

2. **แก้ `_store_instrumental` / `_store_vocal`** ให้ encode ก่อน move เข้า job store
   - เก็บนามสกุลจริงไว้ใน store (เช่น `_jobs[job_id] = (path, expiry)` path ชี้ `.m4a` หรือ `.wav`)
   - ระวัง signature ต่างกัน: `_store_instrumental(src) -> job_id` แต่ `_store_vocal(job_id, src)`
     (vocal ใช้ job_id เดียวกับ instrumental) — encode ทั้งสอง stem
   - encode **ไม่ต้องเข้า** `_inference_lock`: ตอนนี้ `_store_instrumental`/`_store_vocal` ถูกเรียก
     หลังปล่อย lock แล้ว และ ffmpeg AAC เป็นงาน CPU เบา ๆ (ไม่แตะ GPU) เรียกตรงจุดเดิมได้เลย
   - ผลข้างเคียง: `/karaoke` ตอบช้าลงนิดหน่อย (encode 2 stem ~ไม่กี่วินาที) — ยอมรับได้
     เทียบกับ separation ที่กินเป็นนาที ไม่ต้อง optimize

3. **แก้ `get_instrumental` / `get_vocal`**:
   - เลิก `read_bytes()` → กลับไปใช้ `FileResponse` (รองรับ range request ในตัว)
   - media_type ตามไฟล์จริง: `audio/mp4` (m4a) หรือ `audio/wav` (fallback)
   - แก้ `Content-Disposition` ให้ filename ตรงนามสกุลจริงด้วย (ตอนนี้ hardcode
     `instrumental.wav` / `vocals.wav` ที่ `main.py` ~614, ~630)
   - หมายเหตุ: ปัญหา keep-alive เดิมเกิดจากไฟล์ 35 MB — พอไฟล์เหลือ ~3-4 MB ปัญหานั้นหายไปเอง
     แต่**ต้อง test ด้วย Chrome จริงอีกครั้ง** ก่อน merge (ดู acceptance ข้างล่าง)
   - แผนสำรอง: ถ้า ERR_FAILED โผล่อีกกับ `FileResponse` ให้คงแบบอ่านเข้า RAM ไว้ —
     ไฟล์เล็กลง ~10 เท่าแล้ว ต้นทุน RAM ต่อ request ยอมรับได้ (เสีย range request ไป
     แต่ browser เล่น m4a จาก blob เดียวได้ปกติ)

4. **Frontend (`web/player.js`)** — ไม่ต้องแก้ logic (ใช้ `audio.src` อยู่แล้ว เล่น m4a ได้เลย)
   แต่แก้ comment ที่อ้างถึง "30-50 MB WAV" ให้ตรงความจริง

5. **อย่าลืม**: `/karaoke` ยังต้องคืน `instrumental_url` / `vocal_url` path เดิม
   (URL ไม่เปลี่ยน — browser ไม่สนนามสกุล สน `Content-Type`)

### ข้อควรระวัง

- AAC/Opus มี encoder delay เล็กน้อย (~20-50ms) → เนื้อเพลงอาจเหลื่อมนิดหน่อย
  ผู้ใช้ชดเชยได้ด้วย sync offset slider ที่มีอยู่แล้ว — จดไว้ใน docs ก็พอ ไม่ต้องแก้โค้ด
- `/separate` (zip ของ stems) **ไม่ต้องแก้** — อันนั้นผู้ใช้ตั้งใจดาวน์โหลดไฟล์ดิบ
- Dockerfile: เช็คว่า ffmpeg ใน image มี encoder `aac` (built-in มีแน่นอน ไม่ต้องลงเพิ่ม)

### Tests (เพิ่มใน `server/tests/test_api.py` + unit)

- unit: `encode_stem` แปลง wav สั้น ๆ → ได้ไฟล์ m4a ที่ `sf`/ffprobe อ่านได้, เล็กกว่าต้นฉบับ
- unit: encode fail (mock ffmpeg พัง) → fallback คืน wav เดิม + WARNING
- API: `/instrumental/{id}` คืน `Content-Type: audio/mp4` และตอบ range request
  (`Range: bytes=0-99` → 206)
- API เดิมทั้งหมดต้องยังผ่าน

### Acceptance

- ไฟล์ instrumental ที่เสิร์ฟเล็กลง ≥ 8 เท่า ที่คุณภาพฟังไม่ออกว่าต่าง
- เปิดผ่าน Chrome จริง: เล่น + seek ได้ ไม่มี ERR_FAILED
- `STEM_ENCODE=0` ใช้งานได้เหมือนก่อนแก้ทุกประการ

---

## F2 — ปิดลูป "แก้เนื้อแล้ว render วิดีโอใหม่" จากหน้า player

### ปัญหาปัจจุบัน

- M4 edit mode มีครบ (tap-to-sync, แก้คำ, export LRC/JSON ใน `web/player.js`)
- แต่ `POST /render` บังคับอัปโหลดไฟล์ instrumental เอง — ผู้ใช้ที่เพิ่งผ่าน `/karaoke`
  มี instrumental ค้างอยู่บน server แล้ว (job store) ไม่ควรต้องอัปโหลดซ้ำ
- ฝั่ง client ไม่มีตัวสร้าง ASS — ASS ใน payload เดิม stale ทันทีที่แก้เนื้อ

### สิ่งที่ต้องทำ

1. **Endpoint ใหม่ใน `server/app/main.py`** (sync `def` เหมือนตัวอื่น — ffmpeg ต้องอยู่ใน threadpool):
   ```
   POST /render/{job_id}
   body (JSON): { "words": [{text,start,end}, ...] }   # payload ที่แก้แล้วจาก player
   → karaoke.mp4 (FileResponse, BackgroundTask ลบ tmpdir — pattern เดียวกับ /render เดิม)
   ```
   - หา instrumental ด้วย `_get_instrumental(job_id)` → ไม่เจอ/หมดอายุ = 404
     (ใช้ `_err(404, ..., "render")` ให้ shape error เหมือนเดิม)
   - **สร้าง ASS ฝั่ง server** จาก words ที่ส่งมา: reuse `lrc.to_lines` + `lrc.to_ass`
     (อย่าเขียน ASS serializer ใน JS — "reuse, don't reinvent")
     - แปลง words → `Word` objects (`schemas.py`) + จัด line ด้วย logic เดิม
       ดู `_run_pipeline` ขั้น [3]-[4] เป็นตัวอย่าง แต่**ไม่ต้อง tokenize ใหม่** —
       words ที่ผู้ใช้แก้แล้วคือ token แล้ว ให้ group เป็นบรรทัดตามช่องว่างเวลา
       หรือรับ `lines: [[w,...],[w,...]]` จาก client ตรง ๆ (แนะนำแบบหลัง — client มี
       `model.lines` อยู่แล้ว, serialize ด้วยฟังก์ชันใหม่ข้าง `serializePayload`)
   - validate: words ว่าง → 400, job_id ไม่เจอ → 404, render พัง → 500 stage `"render"`
     - **ระวัง error shape**: ถ้ารับ body เป็น Pydantic model, FastAPI จะตอบ 422 รูปแบบ
       ของมันเองเวลา validate fail — ถ้าต้องการ 400 shape `{error, stage}` เหมือน endpoint
       อื่น ให้รับเป็น `dict` แล้ว validate เองด้วย `_err(400, ..., "render")`
       (หรือยอมรับ 422 ก็ได้ แต่ต้องเลือกแล้ว test ให้ตรง)
   - งาน ffmpeg ต้องอยู่ใน `_inference_lock` (NVENC อาจแตะ GPU — ตาม pattern `/render` เดิม)
   - **ต่ออายุ TTL** ก่อน render (ผู้ใช้นั่งแก้เนื้อนานเกิน 10 นาทีได้ง่าย ๆ):
     เพิ่ม helper `_touch_job(job_id)` ที่ reset expiry **ทั้งสอง store** (`_jobs` และ
     `_vocal_jobs` — สอง dict แยกกัน lock คนละตัว) แล้วเรียกมันใน `get_instrumental`
     และ `get_vocal` ด้วย (ทุกครั้งที่มีการใช้งาน = ยังไม่ควรหมดอายุ — ไม่งั้นผู้ใช้
     แก้เนื้อนานแล้วกด vocal guide จะเจอ 404 ทั้งที่ instrumental ยังอยู่)

2. **Frontend (`web/player.js` + `index.html`)**:
   - จำ `job_id` + `apiBase` ไว้หลัง `runKaraoke` สำเร็จ (ตัวแปร module-scope ข้าง `model`)
   - เพิ่มปุ่ม `🎬 สร้างวิดีโอ · Render video` ในแถบเดียวกับ export (ซ่อนถ้าไม่มี `job_id`)
   - กดแล้ว: POST `/render/{job_id}` ด้วย `serializeLines(model.lines)` →
     สถานะ busy ("กำลังเผาวิดีโอ… อาจใช้เวลา 1-3 นาที") → ได้ blob → download `karaoke.mp4`
     (ใช้ helper `download()` ที่มีอยู่ ปรับให้รับ Blob ได้)
   - render fail → `setStatus(..., "error")` พร้อมข้อความจาก `_serverError`
   - ฟังก์ชัน serialize ใหม่ต้อง pure + export (เทสต์ใน `player.test.mjs` ได้แบบไม่แตะ DOM)

3. **เอกสาร**: เพิ่ม endpoint ใหม่ใน `docs/HANDOFF.md` (หัวข้อ Endpoints) + `PRD.md` ส่วน as-built

### Tests

- API: `/render/{job_id}` กับ job จริง (mock `render.render_video` ให้คืนไฟล์ dummy ได้
  ตาม pattern test เดิม) → 200 + `video/mp4`
- API: job_id มั่ว → 404, words ว่าง → 400
- API: เรียก `get_instrumental` แล้ว expiry ถูกต่ออายุ (ทดสอบ `_touch_job`)
- JS (`web/player.test.mjs`): serialize lines → shape ที่ endpoint รับ, round-trip กับ
  `serializePayload` สอดคล้องกัน

### Acceptance

- Flow ครบใน browser เดียว: อัปโหลดเพลง → แก้คำ/เวลา 2-3 จุด → กดปุ่ม render →
  ได้ mp4 ที่เนื้อตรงกับที่แก้ (ไม่ใช่เนื้อเดิม)
- ผู้ใช้แก้เนื้อนาน >10 นาทีแล้วยัง render ได้ (TTL ต่ออายุ)
- ไม่มีการอัปโหลดไฟล์เสียงซ้ำใน flow นี้

---

## F3 — ไฮไลต์คำที่ ASR ไม่มั่นใจ (ชี้เป้าให้แก้เนื้อ)

### ทำไม

ลูกทุ่ง/melisma ทำให้ ASR เพี้ยนเป็นจุด ๆ (PRD §6 ยอมรับไว้แล้ว ทางออกคือ post-edit)
แต่ตอนนี้ผู้ใช้ต้องไล่หาคำผิดเอง — ถ้าแต้มสีคำที่โมเดล "ไม่มั่นใจ" ไว้ให้
จะแก้เนื้อได้เร็วขึ้นมาก และต่อยอด F2 (แก้แล้ว render ใหม่) ตรง ๆ

### ข้อจำกัดสำคัญ — อ่านก่อนออกแบบ

confidence รายคำจาก faster-whisper (`word_timestamps=True`) **map กับ token ไทยไม่ได้ 1:1**
เพราะ pipeline tokenize ใหม่ด้วย PyThaiNLP (`thai.tokenize`) หลัง ASR — "คำ" ของ whisper
กับ "คำ" ของ newmm คนละชุดกัน. **อย่าพยายาม map รายคำ** (scope ใหญ่, เปราะ).
ให้ใช้ **confidence ระดับ segment** แทน: ทุกคำใน segment เดียวกันได้ค่าเดียวกัน —
หยาบกว่าแต่ตรงเป้า เพราะ melisma มักพังทั้งท่อนอยู่แล้ว

### สิ่งที่ต้องทำ

1. **`server/app/asr.py`** — เก็บ confidence ติดมากับ `Segment`:
   - เพิ่ม field `avg_logprob: float | None = None` และ `no_speech_prob: float | None = None`
     ใน `@dataclass Segment` (default None เพื่อไม่พัง caller/test เดิม)
   - ใน `_transcribe_on` อ่าน `s.avg_logprob`, `s.no_speech_prob` จาก faster-whisper
     (มีให้อยู่แล้ว ไม่ต้องเปิด `word_timestamps`)
   - แปลงเป็น confidence 0..1 ด้วย helper pure function (test ง่าย):
     ```
     def segment_confidence(avg_logprob, no_speech_prob) -> float | None
     # แนวทาง: conf = exp(avg_logprob) แล้วหักด้วย no_speech_prob สูง ๆ
     # clamp 0..1; None ถ้าไม่มีข้อมูล
     ```

2. **`server/app/schemas.py`** — เพิ่ม `confidence: float | None = None` ใน `Word`
   (optional → JSON เดิม/ไฟล์ json เก่าที่ผู้ใช้ load เข้า player ยังใช้ได้)

3. **`server/app/main.py` `_run_pipeline` ขั้น [3]** — ตอนสร้าง `seg_words` จาก
   `thai.map_words(...)` ใส่ confidence ของ segment ลงทุกคำในกลุ่มนั้น
   (จุดเดียวที่รู้ทั้ง segment และ words — อย่าไปแก้ `thai.map_words` signature
   ถ้าไม่จำเป็น; set attribute หลังได้)

4. **Frontend (`web/player.js` + `style.css`)**:
   - threshold ผ่านค่าคงที่ `LOW_CONF = 0.55` (export ไว้ปรับ/test ได้)
   - ใน `renderLyrics()`: คำที่ `confidence != null && confidence < LOW_CONF`
     เพิ่ม class `low-conf` → CSS ขีดเส้นใต้หยัก ๆ สีส้ม (อย่าใช้สีแดงล้วน —
     มันคือ "ไม่แน่ใจ" ไม่ใช่ "ผิด") + `title` tooltip บอก % ความมั่นใจ
   - ใน status bar หลัง load: ถ้ามีคำ low-conf ให้บอกจำนวน เช่น
     `"มี 12 คำที่ AI ไม่มั่นใจ (ขีดเส้นใต้สีส้ม) — เปิดโหมดแก้ไขเพื่อตรวจ"`
   - แก้คำแล้ว (`retypeWord`/`syncWordStart`) → ลบ class `low-conf` ออก
     (ผู้ใช้ยืนยันแล้ว) และ `serializeWords` ตัด confidence ทิ้งใน export
     (ไฟล์ที่แก้แล้วถือว่า confirmed — สอดคล้อง `aligned: true, edited: true` เดิม)

### Tests

- unit (pytest): `segment_confidence` กับค่า logprob จริง ๆ (เช่น -0.2 → สูง, -1.5 → ต่ำ,
  no_speech_prob 0.9 → ต่ำ, None → None)
- API: response `/transcribe` มี `confidence` ในแต่ละ word (mock ASR ตาม pattern test เดิม)
- JS (`player.test.mjs`): buildModel เก็บ confidence ผ่าน, payload เก่าไม่มี field → ไม่พัง,
  serializeWords ไม่มี confidence ติดออกไป

### Acceptance

- เพลงทดสอบ luk-thung 1 เพลงจาก `server/tests/samples/`: ท่อนที่ ASR เพี้ยน
  (เทียบ `docs/M0_EVAL_NOTES.md`) ถูกขีดเส้นใต้เป็นส่วนใหญ่
- payload/JSON เดิมที่ไม่มี confidence ยังเล่นได้ปกติ
- ไม่มีการเรียกโมเดลเพิ่ม / ไม่ทำให้ pipeline ช้าลง

---

## F4 — Async job queue: เปลี่ยน `/karaoke` จาก block ยาวเป็น submit → poll

### ทำไม

`/karaoke` ตอนนี้ block HTTP request เดียวยาวได้ ~20 นาที (CPU separation) —
เสี่ยง timeout ของ proxy/browser, refresh แล้วงานหาย, และเปิด public ไม่ได้จริง.
โครง `/progress/{id}` มีอยู่แล้ว → ต่อยอดเป็น job queue เต็มตัวได้โดยแก้น้อย

### หลักการ (คุมขอบเขต!)

- **ไม่เอา dependency ใหม่** (ไม่ใช้ Celery/Redis/RQ) — `threading` + in-memory dict
  ตาม pattern ที่ repo ใช้อยู่ (`_jobs`, `_progress`, sweeper thread). Self-host promise:
  ยังเป็น Docker image เดียว `docker compose up` จบ
- งานยังรันทีละตัวผ่าน `_inference_lock` เดิม — queue คือ "คิวรอ lock" ไม่ใช่ parallelism
- stateless ตามเดิม: ผลงาน (payload + stems) อยู่ใน store TTL-bounded, ไม่เก็บไฟล์ต้นฉบับ

### สิ่งที่ต้องทำ

1. **Endpoints ใหม่ใน `server/app/main.py`** (คง `/karaoke` เดิมไว้เพื่อ backward-compat):
   ```
   POST /jobs/karaoke   (multipart เดิม: file, lang)
     → 202 { "job_id": "...", "status_url": "/jobs/<id>" }   # ตอบทันทีหลังเซฟ upload เสร็จ
   GET  /jobs/{job_id}
     → { "status": "queued|running|done|error",
         "stage": <stage เดิมจาก _progress>, "step": n, "total": 4,
         "result": <payload /karaoke เดิม> | null,   # ใส่เมื่อ done
         "error": {error, stage} | null }
   ```
   - ใช้ `job_id` ตัวเดียวกันทั้ง progress/instrumental/vocal (เลิก gen แยก —
     `_store_instrumental` ต้องรับ job_id จากภายนอกได้; ปรับ signature ให้
     สอดคล้องกับ `_store_vocal(job_id, src)` ไปเลย)
   - worker: `threading.Thread` ต่อ job (หรือ single worker thread + `queue.Queue` —
     **เลือก single worker** จะตรง invariant "ทีละงาน" กว่าและไม่ต้องพึ่ง lock อย่างเดียว)
   - ตัว job รัน logic เดิมของ `/karaoke` (refactor body เป็นฟังก์ชัน `_run_karaoke_job`
     ที่ทั้ง endpoint เก่าและ worker เรียกใช้ — ห้าม copy-paste)
   - upload ถูกเซฟลง temp dir ของ job ก่อนตอบ 202; ลบใน finally ของ worker เหมือนเดิม
   - job record อยู่ใน dict + TTL sweep (ผูกกับ `_sweep_jobs` ที่มีอยู่):
     ผลลัพธ์ done/error เก็บไว้ `JOB_RESULT_TTL_SEC` (default 1800) แล้วกวาดทิ้ง
   - กันคิวบวม: `MAX_QUEUED_JOBS` (default 3) → เกิน = 429 `{error, stage:"queue"}`

2. **Frontend (`web/player.js`)**:
   - `runKaraoke` เปลี่ยนเป็น: POST `/jobs/karaoke` → ได้ job_id → poll `GET /jobs/{id}`
     ทุก 1.5s (แทน `/progress` poll เดิม — status รวม stage แล้ว) → `status: "done"`
     → ใช้ `result` เหมือน payload เดิมทุกประการ (โค้ดหลังจากนั้นไม่ต้องแก้)
   - แสดงตำแหน่งคิวถ้า `queued` (เพิ่ม `queue_position` ใน GET response ได้ — nice to have)
   - **จุดขายสำคัญ**: เก็บ `job_id` ล่าสุดลง `localStorage` → เปิดหน้าใหม่/refresh
     ตอนงานยังวิ่ง แล้วหน้าเว็บ resume การ poll ต่อได้ (แก้ pain "อย่าเพิ่งรีเฟรช" เดิม)
   - fallback: ถ้า POST `/jobs/karaoke` ได้ 404 (server เก่า) → ใช้ `/karaoke` เดิม

3. **เอกสาร**: HANDOFF.md endpoints + PRD as-built + หมายเหตุว่า `/karaoke` เดิม
   deprecated สำหรับ client ใหม่ (ยังไม่ลบ)

### Tests

- API: submit → 202 + job_id; poll จน done (mock separate+ASR ให้เร็วตาม pattern เดิม) →
  result shape ตรงกับ `/karaoke` เดิม (เทียบ field ต่อ field)
- API: งานพัง → status `error` + `{error, stage}` ถูกต้อง
- API: ส่งเกิน `MAX_QUEUED_JOBS` → 429
- API: job done แล้วเกิน TTL → GET คืน 404 (sweep ทำงาน)
- JS: poll loop หยุดเมื่อ done/error, resume จาก localStorage ทำงาน
- ของเดิม: `/karaoke` แบบ block ต้องยังผ่านทุก test เดิม

### Acceptance

- อัปโหลด → ได้ 202 ภายในไม่กี่วินาที (ไม่ block จน pipeline จบ)
- refresh หน้าระหว่างประมวลผล → กลับมาเห็น progress ต่อและได้ผลลัพธ์
- ยิง 2 งานพร้อมกัน → งานที่สอง `queued` รอจนงานแรกจบ (ไม่รันซ้อน — VRAM invariant คงอยู่)
- `docker compose up` ยังจบในตัว ไม่มี service/dependency ใหม่

---

## F5 — Sync quality badge (set expectation ก่อนร้อง)

### ทำไม

ข้อมูลมีครบแล้วใน response: `degraded_segment_count` / `total_segment_count` + `aligned`
(`schemas.py`) — แค่ยังไม่ได้โชว์ให้ผู้ใช้เห็นแบบเข้าใจง่าย. badge ช่วยบอกล่วงหน้าว่า
"เพลงนี้จังหวะแม่นแค่ไหน" และชี้ให้ไปใช้ edit mode เมื่อจำเป็น

### สิ่งที่ต้องทำ (frontend เท่านั้น — backend ไม่ต้องแก้)

1. **`web/player.js`** — เพิ่ม pure function (export, test ได้):
   ```
   export function syncQuality(payload) -> { level: "good"|"partial"|"rough", pct: number|null }
   ```
   - `aligned: false` หรือไม่มี field → `rough`, pct = null
   - คำนวณ `pct = 100 * (1 - degraded/total)` (กัน total = 0)
   - threshold: pct ≥ 80 → `good`, pct ≥ 40 → `partial`, ต่ำกว่า → `rough`
2. **`loadModel()`** — สร้าง badge element (DOM/textContent, ห้าม innerHTML —
   ตาม convention XSS-safe ของไฟล์นี้) วางไว้ข้าง status:
   - 🟢 `จังหวะแม่น 95% · Word-synced`
   - 🟡 `จังหวะโดยประมาณ 60% · Partially synced — เปิดโหมดแก้ไขช่วยปรับได้`
   - 🔴 `จังหวะประมาณเท่านั้น · Estimated timing — แนะนำใช้โหมดแก้ไข`
3. **`index.html` + `style.css`** — ตำแหน่ง badge (แถว status หรือหัว step2), สี 3 ระดับ
4. payload เก่า (ไฟล์ json ที่ไม่มี field เหล่านี้) → ไม่แสดง badge, ห้าม error

### Tests / Acceptance

- JS unit: `syncQuality` ครบทุก branch (aligned false, total 0, ขอบ threshold)
- โหลด payload จาก `server/tests/out_vocals_fixed/*.json` แล้ว badge ขึ้นถูกระดับ

---

## F6 — ติดธง `interpolated` รายคำ (รู้ว่าคำไหน timing เดา)

### ทำไม

`thai.map_words` รู้อยู่แล้วว่าคำไหน match จาก char_map จริงและคำไหนถูก interpolate
(`_match_spans` คืน `None` ต่อ token ที่ไม่ match → `_resolve_spans` เติมด้วยการเดา)
แต่ข้อมูลนี้ถูกทิ้งไป. ส่งให้ frontend = ผู้ใช้เห็นว่า "คำนี้เวลาเดานะ" รายคำ —
ละเอียดกว่า F5 (ระดับเพลง) และเสริม F3 (ความมั่นใจเรื่อง "ข้อความ" ส่วนอันนี้คือ "เวลา")

### สิ่งที่ต้องทำ

1. **`server/app/schemas.py`** — เพิ่ม `interpolated: bool = False` ใน `Word`
   (default False → payload/test เดิมไม่พัง)
2. **`server/app/thai.py`**:
   - `_resolve_spans` คือจุดที่รู้ว่า span ไหนเป็น `None` (เดา) — ให้ติดธงตรงนั้น:
     คำที่ span = None → `interpolated=True`
   - path ที่ไม่มี char_timings เลย (`_interpolate` ทั้ง segment) → ทุกคำ `interpolated=True`
   - ระวัง `_enforce_monotonic` อย่าทำธงหาย (ถ้ามันสร้าง Word ใหม่ ให้ copy ธงตาม)
3. **`web/player.js` + `style.css`**:
   - `renderLyrics()`: word ที่ `interpolated` → class `interp` → CSS opacity จางลง
     (เช่น 0.55) — **คนละ visual กับ F3** (`low-conf` = เส้นใต้ส้ม = ข้อความน่าสงสัย,
     `interp` = จาง = เวลาเดา; ใช้พร้อมกันได้บนคำเดียว)
   - แก้เวลา (`syncWordStart` ผ่าน click/stamp/nudge) → เคลียร์ธง + class
     (ผู้ใช้ตั้งเวลาเองแล้ว) และตัด `interpolated` ทิ้งใน `serializeWords` เหมือน confidence
4. หมายเหตุ scope: **อย่า** เปลี่ยน logic การ interpolate ใด ๆ — งานนี้คือ "ติดธง" เท่านั้น

### Tests / Acceptance

- pytest: `map_words` กับ char_timings ที่ match บางคำ → ธงถูกต้องรายคำ;
  ไม่มี char_timings → ธง True ทุกคำ; monotonic pass ไม่ลบธง
- API: `/transcribe` (mock) มี `interpolated` ใน words
- JS: render จาง, แก้เวลาแล้วหายจาง, export ไม่มี field

---

## F7 — Romanization toggle (สำหรับคนเรียนภาษาไทย)

### ทำไม

PyThaiNLP มี `romanize()` ในตัว (ติดตั้งอยู่แล้ว — เห็นใน `pythainlp.transliterate`)
กลุ่ม Thai learner ร้องตามได้ถ้ามีคำอ่านโรมันใต้คำไทย — ฟีเจอร์ราคาถูก กลุ่มเป้าหมายชัด

### สิ่งที่ต้องทำ

1. **`server/app/thai.py`** — helper ใหม่:
   ```
   def romanize_word(text: str) -> str   # ครอบ pythainlp.romanize(engine="royin")
   ```
   - engine ตั้งผ่าน env `ROMANIZE_ENGINE` (default `royin` — มาตรฐานราชบัณฑิต)
   - **ระวัง dependency**: บาง engine ของ pythainlp ดึง package เพิ่ม — ทดสอบใน
     Docker image ว่า `royin` ใช้ได้โดยไม่ลงอะไรเพิ่ม; ถ้า engine ใช้ไม่ได้ →
     คืน `""` + log WARNING ครั้งเดียว (degrade ไม่พัง pipeline)
2. **เก็บลง `Word` ตรง ๆ** — เพิ่ม `roman: str | None = None` ใน schema
   (ดีกว่า field `words_romanized` แยก array: ไม่ต้อง sync index สองชุด และ
   export json แล้ว roundtrip กลับเข้า player ได้ครบในก้อนเดียว)
   - เติมใน `_run_pipeline` ขั้น [3] (จุดเดียวกับ confidence ของ F3 — ถ้าทำ F3 แล้ว
     ใช้ loop เดียวกัน)
   - ปิดได้ด้วย env `ROMANIZE=0` (default เปิด — งานเบา ระดับ ms ต่อเพลง;
     ถ้า benchmark แล้วช้าผิดคาด ค่อยสลับ default)
3. **Frontend (`web/player.js` + `index.html` + `style.css`)**:
   - toggle `🔤 คำอ่าน · Romanization` ข้าง edit toggle, จำค่าใน `localStorage`
   - เปิดแล้ว: ใต้แต่ละ word span แสดง `<small class="roman">` (สร้างตอน render
     แต่ display ผ่าน CSS class บน body — toggle แล้วไม่ต้อง re-render)
   - `retypeWord` แก้ข้อความ → roman เดิม stale: เคลียร์ roman ของคำนั้น
     (อย่าพยายาม romanize ฝั่ง JS)
4. ASS/วิดีโอ **ไม่ต้องใส่ roman** ใน scope นี้ (จอวิดีโอแน่นแล้ว — ถ้าต้องการค่อยเป็นงานแยก)

### Tests / Acceptance

- pytest: `romanize_word("รัก")` ได้ค่าไม่ว่าง; engine พัง (mock) → `""` ไม่ throw
- API: words มี `roman`; `ROMANIZE=0` → ไม่มี/เป็น None
- JS: toggle แสดง/ซ่อนไม่ re-render, payload เก่าไม่มี roman → ไม่พัง

---

## F8 — ASS style customizer (ปรับหน้าตาวิดีโอก่อน export)

### ทำไม

`lrc.py` hardcode `_ASS_HEADER` (Sarabun 48pt ขาว/ส้ม-น้ำเงิน, PlayResX/Y 1280×720,
Alignment 2 ล่างกลาง) — ผู้ใช้ปรับอะไรไม่ได้เลย. เปิดช่องปรับ font/สี/ขนาด/ตำแหน่ง
ทำให้ video export ใช้งานจริงได้หลากหลายขึ้น

### สิ่งที่ต้องทำ

1. **`server/app/lrc.py`** — เปลี่ยน `_ASS_HEADER` เป็น template:
   ```
   @dataclass(frozen=True)
   class AssStyle:
       font: str = "Sarabun"
       font_size: int = 48
       primary_colour: str = "FFFFFF"   # รับ hex RRGGBB จาก UI
       highlight_colour: str = "FFA500" # สีคำที่กำลังร้อง (SecondaryColour)
       outline_colour: str = "000000"
       alignment: int = 2               # ASS numpad: 2=ล่างกลาง 8=บนกลาง 5=กลางจอ
       margin_v: int = 40

   def to_ass(lines, style: AssStyle = AssStyle()) -> str
   ```
   - helper `_hex_to_ass_colour("RRGGBB") -> "&H00BBGGRR"` — **ASS กลับ byte order
     เป็น BGR** อย่าลืม (จุดพลาดคลาสสิก — ต้องมี unit test)
   - default ทั้งหมด = ค่าเดิมเป๊ะ ๆ → output ของ caller เดิมต้องไม่เปลี่ยนแม้แต่ byte เดียว
     (มี test snapshot เทียบ)
   - validate ฝั่ง construct: font_size 8–200, alignment ∈ 1–9, hex ถูก format →
     ผิด = `ValueError` (endpoint แปลงเป็น 400)
2. **`server/app/main.py`** — endpoint render (ทั้ง `/render` เดิม และ `/render/{job_id}`
   จาก F2) รับ style params เป็น optional form/JSON fields:
   `font, font_size, primary_colour, highlight_colour, alignment, margin_v`
   - ส่งต่อให้ `to_ass(...)` — สำหรับ `/render` เดิมที่รับ `ass` text ตรง ๆ:
     style ใช้ไม่ได้กับ ASS สำเร็จรูป → **ใช้ style ได้เฉพาะเส้นทางที่ server
     สร้าง ASS เอง** (`/render/{job_id}` ของ F2) — ระบุใน docs ให้ชัด
   - font ที่ขอแล้วเครื่องไม่มี: libass จะ fallback เอง แต่ `render.py` force font ผ่าน
     filter `subtitles` อยู่แล้ว (`RENDER_FONT`) — ให้ font param override ค่านั้น
     และจำกัด choice เป็น allowlist (`Sarabun`, `Noto Sans Thai`, + env `RENDER_FONTS_EXTRA`)
     กัน command injection ผ่านชื่อ font ใน ffmpeg filter string
3. **Frontend** — UI เล็ก ๆ ใน panel render (ของ F2): dropdown font, color picker 2 อัน
   (สีปกติ/สีไฮไลต์), ขนาด, ตำแหน่ง (ล่าง/กลาง/บน) → ส่งไปกับ POST render
   - จำค่าใน `localStorage`
   - (nice to have) preview สดด้วย CSS บน lyrics area — ไม่บังคับใน scope นี้

### Tests / Acceptance

- pytest: `_hex_to_ass_colour` (RGB→BGR!), default style → output เท่า snapshot เดิม,
  validate reject ค่าผิด, font นอก allowlist → 400
- API: render พร้อม style params → mp4 ออก (mock ffmpeg ตาม pattern เดิม)
- เปลี่ยนสี/ขนาด/ตำแหน่งแล้ววิดีโอจริงเปลี่ยนตาม (manual check 1 เพลง)

---

## ลำดับงานแนะนำ

1. F1 ก่อน (เล็กกว่า, แตะ store ที่ F2 ต้องใช้ต่อ — ทำก่อนจะได้ไม่ conflict)
2. F2 ต่อ โดย render จาก stem ที่ encode แล้ว (ffmpeg รับ m4a เป็น input ได้ตรง ๆ)
3. **F5 แทรกได้ทุกเมื่อ** — frontend ล้วน ไม่ชนใคร เล็กสุดในไฟล์นี้
4. F3 + F6 ทำคู่กัน (แตะ `Word` schema + loop เดียวกันใน `_run_pipeline` ขั้น [3] —
   ทำพร้อมกันประหยัดกว่าแยก) · F7 ก็แตะจุดเดียวกัน จะพ่วงด้วยก็ได้
5. F8 ทำหลัง F2 (UI อยู่ใน panel render ของ F2)
6. F4 ทำท้ายสุด (แตะ `/karaoke` + job store ที่ F1/F2 แก้ — ทำก่อนจะ conflict กันเอง)
7. รัน test ทั้ง repo (`pytest` + `node --test web/`) ก่อนปิดงานทุกครั้ง

> หมายเหตุรวม: F3/F5/F6/F7 ทั้งหมดเพิ่ม field แบบ **optional + default** ใน schema —
> payload/JSON เก่าต้อง load ใน player ได้เสมอ และ field ที่เป็น runtime hint
> (confidence/interpolated/roman) ไม่ต้องติดออกไปกับไฟล์ export ที่ผู้ใช้แก้แล้ว
