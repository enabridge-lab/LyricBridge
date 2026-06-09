# ปรับความแม่นยำการ sync คำกับเพลง — สเปกแก้สำหรับ Claude Code

> งานนี้แก้ "คำวิ่งไม่ตรงเสียงร้อง". อ่าน `CLAUDE.md` (locked decisions §2) และ `PRD.md` §6–§7 ก่อน.
> **ห้ามแตะ locked decisions** (pure ASR, cloud รับเฉพาะ vocal stem, GPU ห้าม co-resident).
> เรียงงานตามผลกระทบ — ทำ §0 (diagnose) ให้จบก่อนเสมอ แล้วค่อยไล่ §1→§5.

## 0. Diagnose ก่อนแก้ (บังคับ)

รากปัญหาส่วนใหญ่คือ forced alignment ตกไปโหมด interpolate เงียบๆ. ต้องพิสูจน์ก่อนว่าอยู่โหมดไหน.

- เพิ่ม **structured log สรุปต่อ request**: `aligned=<bool>`, `degraded_segments=<n>/<total>`, `asr_model`, `align_model`, `device`. ใส่ใน `main.py` ตอนจบ pipeline.
- เพิ่ม flag `aligned` + `degraded_segment_count` ลงใน response ของ `/transcribe` (schema `TranscribeResponse`) ให้ client เห็น.
- **Acceptance:** รัน 1 เพลงแล้วบอกได้ทันทีว่า aligned จริงกี่ %; ถ้า `aligned=false` ทั้งเพลง → ไป §1 เป็นอันดับแรก.

## 1. ทำให้ forced alignment โหลด/ทำงานจริง (ผลกระทบสูงสุด)

ไฟล์: `server/app/align.py`

ปัญหา: `ALIGN_MODEL=airesearch/wav2vec2-large-xlsr-53-th` ถ้าโหลดล้มเหลว (เน็ต/cache/OOM บน GTX 1650 4GB ~2.9 GiB) ระบบ degrade เงียบๆ → ทุกคำกลายเป็น interpolate.

ต้องทำ:
- แยก device ของ align ออกเป็น env ใหม่ `ALIGN_DEVICE` (default = `resolve_device()`); ให้บังคับ `cpu` ได้เฉพาะ stage นี้ เมื่อ VRAM ไม่พอ (ช้าแต่ได้ char timing จริง — ดีกว่าเดา).
- ทำให้ความล้มเหลวของการ **โหลดโมเดล** ดังขึ้น: log ระดับ ERROR + เก็บเหตุผลไว้ที่ `/version` (`align_load_error`) เพื่อไม่ให้ fail เงียบ. (ยังต้อง degrade graceful — ห้าม crash request.)
- เพิ่ม env `ALIGN_MODEL` ทางเลือกที่ระบุในเอกสาร (เช่น Thai wav2vec2 ตัวอื่น) ให้สลับได้โดยไม่แก้โค้ด.
- เอกสาร `docs/`: วิธี pre-download โมเดล align ตอน build image (Dockerfile) เพื่อตัดความเสี่ยงเน็ตล่มตอน runtime.

Acceptance: เมื่อ align โหลดได้ `aligned=true` และ `degraded_segments` ต่ำ; ถ้า GPU OOM ให้ `ALIGN_DEVICE=cpu` แล้วยังได้ char timing จริง (ไม่ตก interpolate).

## 2. คุณภาพ ASR — ถอดผิด = จับเวลาผิดตาม

ไฟล์: `server/app/asr.py`

- ยืนยันว่า `ASR_MODEL` ชี้ไป Thai-tuned (เช่น `biodatlab/whisper-th-large-v3` หรือ Typhoon) ไม่ใช่ whisper มาตรฐาน — vanilla อ่อนไทย คำผิดทำให้ char ของ `map_words` จับคู่ไม่ตรง.
- เปิด **VAD filter** ของ faster-whisper (`vad_filter=True`) เพื่อตัด intro ดนตรี/ช่วงเงียบออกจาก segment — ลดเคสที่ timestamp ของท่อนกินช่วงไม่มีเสียงร้อง (ตัวการใหญ่ของลูกทุ่งที่มี intro ยาว).
- ตั้ง `condition_on_previous_text=False` ถ้าพบ ASR หลุด/ซ้ำคำในท่อนเอื้อน (ลด hallucination ที่ลากเวลาเพี้ยน).
- เก็บค่าพวกนี้เป็น env ปรับได้ ค่า default ปลอดภัย.

Acceptance: บนชุดทดสอบ 5 เพลง (PRD §7.7) WER/CER ดีขึ้นหรือเท่าเดิม และ segment ไม่กินช่วง intro เงียบ.

## 3. Offset slider ใน player (quick win — เห็นผลทันที)

ไฟล์: `web/player.js`, `web/index.html`

อาการ "วิ่งเร็วไป/ช้าไปเท่าๆ กันทั้งเพลง" = ออฟเซ็ตคงที่ (latency เสียง/หูฟัง/encoding). ให้ผู้ใช้จูนเอง:
- เพิ่ม slider/number input `syncOffsetMs` (ช่วง ±2000ms, step 50).
- ตอนหา active word ให้ใช้ `els.audio.currentTime + offsetSec` แทน `currentTime` ตรงๆ ใน `activeWordIndex(...)` (อย่าไปแก้ค่า `word.start` ในโมเดล — ปรับแค่ตอนเทียบ เพื่อ export ไม่เพี้ยน).
- จำค่าไว้ใน `localStorage` ของผู้ใช้ (player.js เป็นไฟล์ static ใช้ได้).
- **ต้องมี unit test:** `activeWordIndex` เคารพ offset (บวก/ลบ) อย่างถูกต้อง.

Acceptance: เลื่อน slider แล้วไฮไลต์ขยับตามทันทีโดยไม่ต้องรัน pipeline ใหม่; ค่าออฟเซ็ตถูกจำข้ามรีเฟรช.

## 4. ปรับ greedy char-match ใน `map_words` ให้ลื่นขึ้น

ไฟล์: `server/app/thai.py`

ปัญหา: เมื่อตัวอักษรของ token จับคู่ char timing ไม่ได้ ปัจจุบันวางคำเป็นจุดเดียว (`start==end` ที่ปลายคำก่อนหน้า) → คำกระตุก/กระโดด.

ต้องทำ:
- เก็บ "คำที่ match ไม่ได้" เป็นช่วงว่าง แล้ว **กระจายเวลาเชิงเส้น** ระหว่างคำที่ match ได้ก่อนหน้าและถัดไป (interpolate เฉพาะจุด ไม่ใช่ทั้งท่อน) แทนการยุบเป็นจุดเดียว.
- ป้องกัน match ผิดจากสระ/วรรณยุกต์ที่ WhisperX บางทีไม่ปล่อย char: เทียบแบบ normalize (ตัด combining marks ก่อนเทียบ) แต่คง token เดิมไว้แสดงผล.
- คง `_enforce_monotonic` (ห้าม overlap/ถอยหลัง) — เป็น acceptance เดิม.

Acceptance: บนท่อนที่ char match ได้บางส่วน คำที่เหลือมีช่วงเวลาเพิ่มขึ้นเป็นลำดับ ไม่ยุบเป็นจุดเดียว; เทสต์เดิมใน `test_pipeline_units.py` ยังผ่าน + เพิ่มเคสคำ unmatched.

## 5. ลูกทุ่งเอื้อน/ลากเสียง — เสริม post-edit (ยอมรับว่าจะไม่เป๊ะ 100%)

PRD ยอมรับข้อนี้แล้ว. ของเดิมมี tap-to-sync (M4, `syncWordStart`). เสริมให้ใช้งานจริงสะดวก:
- ปุ่มลัด: ระหว่างเล่น กด key (เช่น Space/Enter) เพื่อ "ตอกเวลา" คำที่ไฮไลต์อยู่ให้ตรงตำแหน่งเสียงปัจจุบัน (เหมือนเครื่องทำ LRC).
- ปุ่ม nudge ±50ms ต่อคำที่เลือก.
- export LRC/JSON ที่แก้แล้วยังทำงานเดิม.

Acceptance: แก้คำที่เอื้อนเพี้ยนด้วยมือได้เร็ว แล้ว export ออกได้ถูกต้อง.

## ลำดับงาน (Build order)

1. §0 diagnose (log + flag) — ต้องมีก่อน เพื่อวัดผลข้อถัดไปว่าดีขึ้นจริง.
2. §1 ทำให้ align โหลดจริง (ผลเยอะสุด) → วัดซ้ำด้วย §0.
3. §3 offset slider (เล็ก เห็นผลไว ให้ผู้ใช้ได้ใช้ระหว่างรอข้ออื่น).
4. §2 ASR/VAD tuning → วัดด้วยชุด 5 เพลง (PRD §7.7).
5. §4 ปรับ char-match.
6. §5 post-edit เสริม.

## วัดผล (อย่าเดาว่าดีขึ้น)

ใช้ harness เดิม `server/tests/run_eval.py` กับชุด 5 เพลงลูกทุ่ง. เพิ่มเมตริก **median word-start error (วินาที)** เทียบกับ LRCLIB line-timing (PRD §7.7 — line-level เท่านั้น ใช้ได้แค่ระดับบรรทัด) หรือ ground-truth ที่จูนมือไว้. รายงานก่อน/หลังทุกข้อ. **device cpu path ต้องผ่าน** (PRD §5).
