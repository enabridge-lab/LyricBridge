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
`/instrumental/<job_id>` · `/progress/<id>` · `/render`

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

## งานที่ค้าง / ทำต่อ

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
