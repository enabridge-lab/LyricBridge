# ลดเวลา process — Performance tuning spec สำหรับ Claude Code

> เป้าหมาย: ลดเวลาต่อเพลงจาก ~20 นาที (CPU) ให้เหลือไม่กี่นาที โดยไม่พังคุณภาพ sync.
> อ่าน `CLAUDE.md` (§5 dev constraints, GTX 1650 4GB) และ `PRD.md` §5.1 ก่อน.
> **ห้ามแตะ locked decisions §2.** ทุก stage ต้องมี `--device cpu` path ที่ใช้ได้เสมอ (self-host ไม่มี GPU).
> ทำ §0 (วัดก่อน) ให้จบก่อนเสมอ แล้วไล่ §1→§4 ตามผลกระทบ.

## 0. วัดเวลาแต่ละ stage ก่อน (บังคับ — อย่าเดาว่าตรงไหนช้า)

ไฟล์: `server/app/main.py`, `server/app/separate.py`, `server/app/asr.py`, `server/app/align.py`

- ใส่ timing รอบแต่ละ stage: `separate`, `asr`, `align`, `tokenize+build`, (และ `render` ถ้ามี).
  ใช้ `time.perf_counter()` ครอบ แล้ว log เป็น structured line เดียว เช่น:
  `timing: separate=1180.3s asr=92.1s align=210.4s build=0.4s device=cpu model=htdemucs_ft.yaml`.
- เพิ่ม timing เหล่านี้ลง response ของ `/karaoke`/`/transcribe` เป็น field `timings_sec` (optional, ปิดได้ด้วย env `EXPOSE_TIMINGS=0`) เพื่อวัดจาก client ได้.
- **Acceptance:** รัน 1 เพลงแล้วบอกได้ว่าเวลาหายไปที่ stage ไหนกี่ %. (คาดว่า `separate` กินส่วนใหญ่.)

## 1. เปิด GPU path ให้ใช้งานจริง (ผลกระทบสูงสุด)

ไฟล์: `server/app/separate.py`, `server/app/asr.py`, `server/app/align.py`, `docs/`

เครื่อง dev มี GTX 1650 4GB แต่ทุก stage default = CPU → นี่คือเหตุผลหลักที่ช้า. ต้องทำให้ GPU
ใช้ได้จริงและปลอดภัยกับ VRAM 4GB:

- เอกสาร env ชุด "โหมดเร็ว" ให้ชัดใน `docs/` + `server/README.md`:
  ```
  SEPARATION_DEVICE=cuda
  ASR_DEVICE=cuda          # faster-whisper -> int8_float16
  ALIGN_DEVICE=cuda        # ถ้า OOM -> ตั้ง cpu เฉพาะ stage นี้ (มีอยู่แล้วใน align.py)
  ```
- ยืนยันวินัย VRAM (PRD 5.1) ยังอยู่: stage แยกกัน + `free_model()` + `cuda_cleanup()` ระหว่างขั้น
  (Demucs/Whisper/align ห้าม co-resident บน 4GB). ตรวจว่า `_inference_lock` serialize ครบ.
- เพิ่ม **OOM fallback อัตโนมัติ**: ถ้า stage บน cuda เจอ CUDA OOM (`torch.cuda.OutOfMemoryError` /
  RuntimeError "out of memory") ให้ `cuda_cleanup()` แล้ว retry stage นั้นบน CPU หนึ่งครั้ง + log WARNING.
  (กันงานล้มทั้งเพลงเพราะ 4GB ไม่พอบางเพลง.)
- ยืนยัน `compute_type`: `int8_float16` (GPU) / `int8` (CPU) ใน asr.py ตาม PRD §5.

Acceptance: บนเครื่อง GPU เวลา `separate` ลดอย่างมีนัย (คาดเหลือหลักนาที); ถ้าไม่มี GPU ทุกอย่างยังรันบน CPU ได้เหมือนเดิม; OOM ไม่ทำให้ request ล้ม.

## 2. โมเดลแยกเสียงที่เบากว่า (ผลกระทบสูง — แลกคุณภาพเล็กน้อย)

ไฟล์: `server/app/separate.py`

`SEPARATION_MODEL` default = `htdemucs_ft.yaml` = ensemble 4 โมเดล (ช้ากว่าตัวเดียว ~4x). ให้สลับได้ง่ายและทดสอบเทียบ:

- รองรับ + เอกสาร 3 พรีเซ็ตผ่าน `SEPARATION_MODEL`:
  - `htdemucs_ft.yaml` — คุณภาพสูงสุด, ช้าสุด (คงเป็น default ได้ หรือย้าย default เป็นตัวเร็ว — **ถามเจ้าของก่อนถ้าจะเปลี่ยน default**).
  - `htdemucs.yaml` — Demucs ตัวเดียว, เร็วขึ้น ~4x, คุณภาพลดเล็กน้อย.
  - โมเดล **2-stem MDX/roformer** (เช่น `UVR-MDX-NET_Karaoke_2.onnx` หรือ mel-band roformer) — คืน vocals+instrumental ตรงๆ, เร็ว, มักคุณภาพเสียงร้องดี.
- เมื่อใช้โมเดล 2-stem: `_classify_stems` จะได้ `instrumental` มาตรงๆ → `_resolve_instrumental` ข้าม `_derive_instrumental` (ไม่ต้องรวม+clip) อยู่แล้ว. ตรวจว่า path นี้ทำงานและไม่รัน summing โดยเปล่าประโยชน์.
- เพิ่ม smoke เทียบเวลา+ขนาดไฟล์ของ 2–3 โมเดลบนเพลงสั้น (`server/tests/separate_smoke.py` ขยายได้).

Acceptance: สลับโมเดลด้วย env ตัวเดียวได้; โมเดล 2-stem ไม่เข้า path summing; มีตัวเลขเวลาเทียบให้ตัดสินใจ.

## 3. ลดงาน ASR

ไฟล์: `server/app/asr.py`

- เปิด **VAD** (`vad_filter=True`, faster-whisper) เป็น default ปรับปิดได้ด้วย env — ข้ามช่วงดนตรี/เงียบ
  ลดความยาวเสียงที่ถอด (เร็วขึ้น + ตรงกับ `docs/SYNC_ACCURACY.md` §2, ช่วยทั้งเวลาและความแม่น).
- รองรับโมเดลเล็กลงเป็นทางเลือกผ่าน `ASR_MODEL` (เช่น Thai-tuned `medium`/distil) + เอกสาร trade-off
  ความแม่น vs เวลา. **ห้ามเปลี่ยน default เป็นตัวเล็กโดยไม่ถามเจ้าของ** (กระทบ M0 eval).
- `beam_size` ลดได้ (เช่น 5→1) เป็น env สำหรับโหมดเร็ว — เร็วขึ้นแลกความแม่นเล็กน้อย.

Acceptance: เปิด/ปิด VAD ได้ด้วย env; โหมดเร็ว (โมเดลเล็ก/beam ต่ำ) รันได้และวัดเวลาเทียบกับ default ได้; M0 5-song eval ยังรันผ่าน.

## 4. เร่ง render วิดีโอ (เฉพาะ /render)

ไฟล์: `server/app/render.py`

- ปัจจุบัน `libx264 -preset veryfast` (CPU). GTX 1650 (Turing) มี **NVENC** → รองรับ `h264_nvenc`
  ผ่าน env `RENDER_VCODEC` (default `libx264`; ตั้ง `h264_nvenc` เพื่อ encode บน GPU).
- ต้อง fallback: ถ้า ffmpeg ไม่มี nvenc (เครื่อง CPU/ไม่มี GPU) ให้ตรวจจับแล้วถอยกลับ `libx264` + log WARNING — ห้าม render ล้มเพราะ codec ไม่มี.
- คง `force_style=FontName=<Thai font>` เดิม (PRD Thai rule — กัน tofu).

Acceptance: ตั้ง `RENDER_VCODEC=h264_nvenc` แล้ว render เร็วขึ้นบนเครื่อง GPU; เครื่องไม่มี nvenc ถอยกลับ libx264 อัตโนมัติ ไม่ล้ม.

## ลำดับงาน (Build order)

1. §0 timing — ต้องมีก่อน เพื่อวัดผลทุกข้อถัดไป.
2. §1 GPU path + OOM fallback (ผลเยอะสุด) → วัดซ้ำด้วย §0.
3. §2 โมเดลแยกเสียงเบา → smoke เทียบเวลา/คุณภาพ.
4. §3 VAD + โหมดเร็ว ASR.
5. §4 NVENC render.

## วัดผล (อย่าเดาว่าเร็วขึ้น)

รายงาน **เวลาต่อ stage ก่อน/หลัง** บนเพลงทดสอบเดียวกัน (เครื่อง GPU และ CPU แยกกัน). ใช้
`server/tests/run_eval.py` (5 เพลง) ยืนยันว่าความเร็วที่เพิ่มไม่ทำ WER/CER แย่ลงเกินรับได้ และ
**device cpu path ต้องผ่านทุกข้อ** (PRD §5 — self-host). สรุปเป็นตาราง preset → (เวลา, คุณภาพ).
