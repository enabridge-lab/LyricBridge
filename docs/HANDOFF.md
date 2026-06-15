# HANDOFF — สรุปโปรเจกต์สำหรับ Agent / Session ใหม่

> วางไฟล์นี้ให้ Agent อ่านก่อนเริ่มงาน จะเข้าใจทั้งโปรเจกต์ในไฟล์เดียว.
> อ่านคู่กับ `CLAUDE.md` (วิธีทำงานกับ repo) และ `PRD.md` (สิ่งที่ต้องสร้าง).

## โปรเจกต์คืออะไร

Open-source **MIT** app (ชื่อที่กำลังพิจารณา: **LyricBridge**): อัปโหลดเพลง → ตัดเสียงร้องออก →
สร้างเนื้อเพลงไทยที่ sync ทีละคำ → เล่นใน web player (ไฮไลต์คำเรียลไทม์) + export วิดีโอคาราโอเกะ.
**โฟกัสภาษาไทย / ลูกทุ่ง ซึ่งเป็นทั้งจุดยากและจุดขายหลัก.**

## สถาปัตยกรรม

Hybrid — แยกเสียงบนเครื่อง, ASR + sync แยกเป็น stage. Cloud รับเฉพาะ **vocal stem** เท่านั้น.
ใช้ **pure ASR เท่านั้น** (ห้ามดึงเนื้อเพลงออนไลน์ / ห้ามบังคับ paste โดยไม่ขออนุญาตเจ้าของ).
บริการเป็น stateless — ประมวลผลใน temp dir แล้วลบ ไม่เก็บไฟล์เสียงผู้ใช้.

## Pipeline (ลำดับ stage + โมเดล)

1. **separate** — python-audio-separator (Demucs). default = `htdemucs.yaml` (โมเดลเดียว,
   เร็วกว่า ensemble `htdemucs_ft` ~4x บน CPU). คืน vocals + instrumental.
2. **asr** — faster-whisper, โมเดลไทยตั้งผ่าน env `ASR_MODEL` (Thai whisper CT2). VAD จูนสำหรับ
   melisma ลูกทุ่ง (gentle thresholds กันการตัดสระร้องยาว). คืน segment + เวลาหยาบ.
3. **align** — WhisperX wav2vec2 (`airesearch/wav2vec2-large-xlsr-53-th`) → timing ระดับตัวอักษร.
   ~2.9GB, OOM ง่ายบน 4GB. ถ้าล้ม degrade เป็น interpolation (เดาเวลาตามความยาวคำ).
4. **thai tokenize** — PyThaiNLP newmm (ไทยไม่มีเว้นวรรค) + map คำเข้ากับเวลา (`thai.map_words`).
5. **build** — สร้าง LRC + ASS (`\k` karaoke tags).
6. **render** — ffmpeg เผา ASS ทับ instrumental → mp4 (libx264 default / `h264_nvenc` บน GPU,
   fallback กลับ libx264 อัตโนมัติถ้าไม่มี nvenc).

## Endpoints

`/healthz` · `/version` · `/transcribe` · `/separate` · `/karaoke` (one-upload flow) ·
`/jobs/karaoke` + `/jobs/<job_id>` (async queue — แนะนำสำหรับ client ใหม่) ·
`/instrumental/<job_id>` · `/vocal/<job_id>` · `/progress/<id>` · `/render` ·
`/render/<job_id>` (re-render จาก instrumental ที่ park ไว้)

- **Stem ถูกบีบเป็น M4A/AAC ก่อนเสิร์ฟ** (F1): `STEM_BITRATE` (default `128k`),
  ปิดด้วย `STEM_ENCODE=0` → กลับไปเสิร์ฟ WAV ดิบ. encode ล้ม → fallback WAV + WARNING.
  หมายเหตุ: AAC มี encoder delay ~20-50ms — ผู้ใช้ชดเชยด้วย sync offset slider ได้.
- **`POST /render/<job_id>`** (F2): body JSON `{"lines": [[{text,start,end},...],...]}`
  (หรือ `{"words":[...]}` แบบ flat) → `karaoke.mp4`. ใช้ instrumental ที่ค้างใน job store
  (ไม่ต้องอัปโหลดซ้ำ); ASS สร้างฝั่ง server ด้วย `lrc.to_lines`/`to_ass`.
  ทุกการเข้าถึง job (`/instrumental`, `/vocal`, `/render/<id>`) ต่ออายุ TTL อัตโนมัติ
  (`_touch_job`) — นั่งแก้เนื้อเกิน 10 นาทีแล้วยัง render ได้.
- **Async queue** (F4): `POST /jobs/karaoke` (multipart เดิม) → 202 `{job_id, status_url}`
  ทันที แล้ว poll `GET /jobs/<id>` → `{status: queued|running|done|error, stage, step,
  result, error, queue_position}`. worker เดียว (FIFO, `queue.Queue` + thread — ไม่มี
  dependency ใหม่), งานยังรันทีละตัวตาม VRAM invariant. `MAX_QUEUED_JOBS` (default 3)
  เกิน → 429 stage `"queue"`; ผลลัพธ์เก็บ `JOB_RESULT_TTL_SEC` (default 1800) แล้วกวาดทิ้ง.
  frontend จำ job ลง localStorage → refresh แล้ว resume poll ต่อได้.
  **`POST /karaoke` แบบ block = deprecated สำหรับ client ใหม่** (ยังไม่ลบ — backward compat).
- **Confidence** (F3): ทุก word ใน response มี `confidence` 0..1 ระดับ segment
  (จาก `avg_logprob`/`no_speech_prob` ของ faster-whisper — ไม่เรียกโมเดลเพิ่ม);
  player ขีดเส้นใต้ส้มคำที่ < 0.55 ชี้เป้าให้แก้เนื้อ. payload เก่าไม่มี field → ใช้ได้ปกติ.
- **Word hints เพิ่มเติม** (F5–F7, ทั้งหมด optional + default — payload เก่าใช้ได้เสมอ):
  - F5 `syncQuality()` (player) แปลง `aligned` + `degraded/total_segment_count`
    เป็น badge 🟢/🟡/🔴 บอกความแม่นจังหวะก่อนร้อง
  - F6 `Word.interpolated` — timing คำนี้เดา (ติดธงใน `thai.py` จุด interpolate);
    player แสดงจาง (opacity) แยกแกนกับ F3, แก้เวลาแล้วธงหาย
  - F7 `Word.roman` — คำอ่านโรมัน (PyThaiNLP `royin`, env `ROMANIZE_ENGINE`;
    ปิดด้วย `ROMANIZE=0`); toggle 🔤 ใน player (CSS class, ไม่ re-render)
  - hint ทั้งสาม (confidence/interpolated/roman) **ไม่ติดออกไป**กับไฟล์ export ที่แก้แล้ว
- **ASS style** (F8): `POST /render/<job_id>` รับ field เสริม `font, font_size,
  primary_colour, highlight_colour, alignment, margin_v` (hex RRGGBB ไม่มี `#`;
  alignment = ASS numpad 2/5/8) → `lrc.AssStyle` (default = header เดิม byte-identical,
  มี snapshot test). font จำกัด allowlist `Sarabun`/`Noto Sans Thai` + env
  `RENDER_FONTS_EXTRA` (กัน injection เข้า ffmpeg filter). `/render` เดิมรับ ASS
  สำเร็จรูป → style ใช้ไม่ได้กับเส้นทางนั้น. UI: panel ใต้ปุ่ม 🎬 (จำค่าใน localStorage).

## เครื่อง dev + กฎ VRAM

Ubuntu, Ryzen 7 3750H, 32GB RAM, **GTX 1650 4GB**.
- ห้าม Demucs + Whisper อยู่บน GPU พร้อมกัน — รัน sequential, `free_model()` ระหว่าง stage.
- `compute_type`: int8_float16 (GPU) / int8 (CPU).
- Demucs `segment_size=7`, `shifts=1` กัน OOM.
- **ทุก stage ต้องมี `--device cpu` path ที่ใช้ได้เสมอ** (self-host ไม่มี GPU; correctness ก่อน speed).

## Locked decisions (ห้ามแก้โดยไม่ขอเจ้าของ — PRD §2)

Hybrid architecture · pure ASR · MVP = web player **และ** วิดีโอ · MIT license ·
build order M0 → M1 → M2 → M3 → M4.

## สถานะปัจจุบัน

- **F1–F8 implement เสร็จแล้ว** (commit `17211b4`):
  stem encode m4a (F1), edit→re-render `/render/<job_id>` (F2), confidence hint (F3),
  async queue `/jobs/*` (F4), sync badge (F5), interpolated flag (F6), romanization (F7),
  ASS style customizer (F8) — รายละเอียด behavior อยู่ในหัวข้อ Endpoints ข้างบน.
- M0 + separation + one-upload `/karaoke` + sync improvements + perf tuning **สร้างเสร็จ, 50 tests ผ่าน**.
- default แยกเสียงเปลี่ยนเป็น `htdemucs.yaml` แล้ว (เจ้าของอนุมัติ, 4x เร็วขึ้นบน CPU).
  หมายเหตุ: M0 eval เดิมใช้ `htdemucs_ft.yaml`.
- **GPU บน Docker ใช้ไม่ได้** (container เป็น torch cpu-only, ไม่มี nvidia runtime).
  กำลังลองเส้น **host venv + GPU** แทน — ตอนนี้ดิสก์ว่าง ~28GB พอลง CUDA torch แล้ว.
- Mechanical fixes เสร็จ: `separate.free_model()` ปล่อย VRAM ก่อน ASR/align,
  tap-to-sync offset (player.js), `_take_instrumental` pop under lock.
- **Align OOM policy ตัดสินแล้ว = Option 1**: OOM ตอนโหลด aligner → retry ทั้ง stage บน CPU
  อัตโนมัติ (ให้เหมือน asr/separate) + log WARNING. เหตุผล: "ตรงแต่ช้า ดีกว่าเร็วแต่มั่ว".
  ถ้าจะมีโหมด GPU-only จริง ๆ ในอนาคต ให้เพิ่ม env `ALIGN_STRICT=1` แยก ไม่ overload `ALIGN_DEVICE=cuda`.
- มี bilingual `docs/PIPELINE.md` (อังกฤษ + ไทย) อธิบาย pipeline ทั้งหมดเขียนไว้แล้ว.

## แผน Deploy production (Modal) — track ใหม่

มีแผน E2E ครบแล้วใน **`docs/MODAL_DEPLOYMENT.md`** + ปฏิบัติการ/rollback ใน
**`docs/RUNBOOK.md`**. สรุปสั้น:

- เป้าหมาย: hosted demo/production สาธารณะบน **Modal** (serverless GPU, scale to zero,
  เครดิตฟรี $30/เดือน ≈ 450-500 เพลง) + frontend static บน Cloudflare/GitHub Pages (ฟรี).
- GPU ที่พอ = **T4** (`gpu=["T4","L4","any"]`, `max_containers=1`, `timeout=900`).
- **Design ถูกบังคับโดยข้อจำกัด Modal**: HTTP จำกัด 150 วิ + กลไกต่ออายุใช้กับ CORS
  ไม่ได้ → ต้องใช้ **spawn + poll** (web endpoint CPU spawn GPU function); web/GPU
  ไม่แชร์ดิสก์ → stems ส่งผ่าน `modal.Dict` (F1 m4a ทำให้ของเล็กพอแล้ว ✅).
  **API shape ของ web app บน Modal ให้ตรงกับ F4 (`/jobs/*`)** — client โค้ดเดียวใช้สองที่.
- โครงแผน: Part B = D0–D5 (setup → Image → Volume โมเดล → spawn+poll API →
  frontend → guardrails) / Part C = P1–P5 (CI/CD GitHub Actions → secrets →
  observability+canary → security/privacy → runbook+rollback). ทุกขั้นมี acceptance.
- ⚠️ **เพลงเต็มขึ้น cloud** ใน deploy นี้ — เจ้าของอนุมัติแล้ว *เฉพาะ hosted demo*
  (ข้อยกเว้นของ locked decision "cloud ได้แค่ vocal stem"; self-host ไม่เปลี่ยน).
- **Blocker — รอเจ้าของ (Part A ของแผน)**: `modal setup` login / push repo ขึ้น GitHub +
  สิทธิ์ Actions secrets / เลือก Cloudflare หรือ GitHub Pages / ตอบ 4 คำถาม
  (โดเมน, ชื่อ public, เพดานงบ, ล็อก region ไหม) — **Claude Code ห้ามเริ่ม D0 จนกว่าจะครบ
  และห้าม mock/เดาแทน**.

## งานที่ค้าง / ทำต่อ

0. **Deploy track**: รอเจ้าของส่งมอบ Part A (ดูหัวข้อข้างบน) → แล้วเริ่ม
   `docs/MODAL_DEPLOYMENT.md` ตามลำดับ D0 → D5 → P1 → P5.
1. **Publish โมเดล Thai Whisper ขึ้น Hugging Face** (เช่น `champkrap/whisper-th-large-v3-ct2`),
   **pin revision (commit hash)**, ใส่เครดิต license ต้นทาง → ให้ `git clone` มาแล้วรันเหมือน
   เครื่องเจ้าของเป๊ะทั้ง GPU/CPU (faster-whisper auto-download + cache ใน `~/.cache/huggingface`).
   *เลือกวิธีนี้แทน Git LFS (ทะลุโควต้า/เสียเงิน) และแทน fetch script (ทำซ้ำของที่ HF ฟรีอยู่แล้ว).*
2. **Pre-publish hygiene** (ยังไม่ใช่ git repo): `.gitignore` ต้องกัน
   - `models/` (~3.2GB — โมเดลก้อนใหญ่, เกิน 100MB/file ของ GitHub)
   - `samples_vocals/` (~257MB — **vocal stem ของเพลงไทยมีลิขสิทธิ์ ห้ามขึ้น GitHub เด็ดขาด**)
   - `server/.venv/`, `~/.cache`, `docker-compose.override.yml` (เครื่องเฉพาะ)
3. Deploy htdemucs speedup เข้า container ที่รันอยู่ + ยืนยัน align model โหลดครบ
   (เช็ก `/version` → `align_load_error` ต้องเป็น `null`).
4. (ค้างจาก review, ยังไม่เร่ง) ตรวจ `player.js` ว่า offset ไม่ถูกนับซ้ำตอน export.

## ข้อกำหนดสำคัญตอน publish

- ต้องรันเหมือนเดิมเป๊ะหลัง `git clone` ทั้ง GPU + CPU (โมเดลต้องดึงตัวเดียวกันกลับมา).
- ห้าม commit ไฟล์เสียงมีลิขสิทธิ์ (เครดิตการแยก stem ไม่เปลี่ยนลิขสิทธิ์เพลง).
- ห้าม commit โมเดลก้อนใหญ่ — แจกผ่าน Hugging Face.
- self-host = Docker image เดียวรันบน CPU ได้ + frontend เป็น static site; `docker compose up` ต้อง "just work".

## เริ่มตรงไหน

อ่าน `CLAUDE.md` → `PRD.md` (โดยเฉพาะ §2 locked decisions, §5 dev constraints, §6 Thai rules, §7 M0).
M0 อยู่ใน `server/` ทั้งหมด. validate ตาม acceptance criteria (โดยเฉพาะ M0 5-song luk-thung eval) ก่อนขยาย scope.

ถ้างานคือ **deploy**: อ่าน `docs/MODAL_DEPLOYMENT.md` + `docs/MODAL_RULES.md` + `docs/RUNBOOK.md`.
ถ้างานคือ **ฟีเจอร์**: F1–F8 ทำครบแล้ว (ดู Endpoints ข้างบน) — ฟีเจอร์ใหม่ให้คุย
กับเจ้าของก่อน (กัน scope creep ตาม CLAUDE.md).
