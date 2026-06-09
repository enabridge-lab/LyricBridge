# 🎤 How LyricBridge Works — End-to-End / กลไกการทำงานทั้งหมด

> **Bilingual / สองภาษา.** Each section is written in **English first (🇬🇧)** then
> **Thai (🇹🇭)**. This document explains the *whole* process: every model, what it
> does, and how each stage hands its output to the next.
>
> เอกสารนี้อธิบาย **กระบวนการทั้งหมด** ของระบบ: ใช้โมเดลอะไรบ้าง แต่ละตัวทำหน้าที่อะไร
> และส่งผลลัพธ์ต่อไปยังขั้นถัดไปอย่างไร — เขียนภาษาอังกฤษก่อนแล้วตามด้วยภาษาไทยในทุกหัวข้อ

---

## 1. The big picture / ภาพรวม

🇬🇧 You upload a song. The app removes the music, transcribes and time-aligns the
Thai lyrics, and gives you back **(a)** a browser player that lights up each word
on beat and **(b)** an exportable karaoke video. Thai / luk-thung (ลูกทุ่ง) is the
priority because it is the hardest case for automatic transcription.

🇹🇭 ผู้ใช้อัปโหลดเพลงเข้ามา ระบบจะ "ลบดนตรีออก" แล้วถอดเนื้อร้องภาษาไทยพร้อม
จับจังหวะเวลาของแต่ละคำ จากนั้นคืนผลลัพธ์เป็น **(ก)** เครื่องเล่นบนเว็บที่ไฮไลต์
ทีละคำตามจังหวะเพลง และ **(ข)** วิดีโอคาราโอเกะที่ดาวน์โหลดได้ โดยให้ความสำคัญ
กับเพลงไทย/ลูกทุ่งเป็นพิเศษ เพราะเป็นกรณีที่ถอดเสียงอัตโนมัติได้ยากที่สุด

```
                 ┌──────────────────────────── on the cloud / บนเซิร์ฟเวอร์ ────────────────────────────┐
full song          [1] separate        [2] ASR            [3] align          [4] tokenize     [5] build
เพลงเต็ม      ─►   Demucs        ─►    faster-whisper  ─►  WhisperX       ─►  PyThaiNLP    ─►  LRC + ASS
                 vocals + music       Thai text + time    char timings        word spans       (lyrics file)
                       │
                       └─ instrumental / ดนตรีล้วน ──────────────────────────────────────────────┐
                                                                                                  ▼
                                                                            [6] render (ffmpeg) ─► karaoke .mp4
                                                                            [7] web player ─────► live highlight
```

🇬🇧 Stages **1–5 run as one sequential pipeline on the server** (stateless: your
audio lives in a temp folder and is deleted right after). Stage 6 (video) and
stage 7 (player) are the two ways you consume the result.

🇹🇭 ขั้นที่ **1–5 ทำงานต่อเนื่องกันบนเซิร์ฟเวอร์** แบบ stateless (ไฟล์เสียงของคุณ
อยู่ในโฟลเดอร์ชั่วคราวและถูกลบทิ้งทันทีหลังประมวลผลเสร็จ) ส่วนขั้นที่ 6 (วิดีโอ) และ
ขั้นที่ 7 (เครื่องเล่นเว็บ) คือสองช่องทางในการนำผลลัพธ์ไปใช้

---

## 2. The models — what each one is and does / โมเดลที่ใช้ — แต่ละตัวทำอะไร

| Stage / ขั้น | Library | Model (default) | Input → Output |
|---|---|---|---|
| 1. Separation / แยกเสียง | [`python-audio-separator`](https://github.com/nomadkaraoke/python-audio-separator) | **Demucs** `htdemucs` (or `htdemucs_ft`, or UVR‑MDX‑NET Karaoke 2) | song → `vocals.wav` + `instrumental.wav` |
| 2. ASR / ถอดเสียง | [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper) | **Whisper** `large-v3` (swap a Thai‑tuned model via `ASR_MODEL`) | vocal wav → text segments + coarse time |
| 3. Alignment / จับเวลา | [`WhisperX`](https://github.com/m-bain/whisperX) | **wav2vec2** `airesearch/wav2vec2-large-xlsr-53-th` | segment text + audio → **per‑character** timing |
| 4. Tokenize / ตัดคำ | [`PyThaiNLP`](https://github.com/PyThaiNLP/pythainlp) | **newmm** dictionary tokenizer | Thai text → word list → **per‑word** spans |
| 5. Build / สร้างไฟล์ | (in‑house `lrc.py`) | — | word spans → `.lrc` + `.ass` |
| 6. Render / ทำวิดีโอ | `ffmpeg` + `libass` | — | instrumental + `.ass` → `.mp4` |
| 7. Player / เครื่องเล่น | static JS (`web/`) | — | `.lrc`/words → live word highlight |

🇬🇧 **Why so many models?** No single model does the job. Whisper gives *text*
but its word timestamps are too rough for sung Thai. wav2vec2 gives precise time
*per character*. PyThaiNLP tells us where each Thai *word* begins and ends (Thai
has **no spaces between words**). Only by combining all three do we get an accurate
**start/end time for every word** — the prerequisite for word‑by‑word highlighting.

🇹🇭 **ทำไมต้องใช้หลายโมเดล?** เพราะไม่มีโมเดลตัวเดียวที่ทำได้ครบ — Whisper ให้
"ข้อความ" แต่เวลาของแต่ละคำหยาบเกินไปสำหรับเสียงร้องภาษาไทย ส่วน wav2vec2 ให้เวลา
ที่แม่นยำระดับ "ตัวอักษร" และ PyThaiNLP บอกว่าคำไทยแต่ละคำเริ่มและจบตรงไหน (เพราะ
ภาษาไทย **ไม่มีการเว้นวรรคระหว่างคำ**) เมื่อนำทั้งสามมารวมกันจึงได้ "เวลาเริ่ม‑จบ
ของทุกคำ" อย่างแม่นยำ ซึ่งจำเป็นต่อการไฮไลต์ทีละคำ

---

## 3. Stage by stage / อธิบายทีละขั้น

### Stage 1 — Separation (แยกเสียงร้องออกจากดนตรี) · `server/app/separate.py`

🇬🇧 Demucs (via `python-audio-separator`) splits the uploaded song into a clean
**vocal** stem and an **instrumental** stem. Some models emit two stems directly
(UVR‑MDX‑NET Karaoke 2); the 4‑stem Demucs emits Vocals/Drums/Bass/Other, so we
**sum the non‑vocal stems** into one instrumental (`_derive_instrumental`). Video
files (`.mp4`, `.mkv`, …) are demuxed to audio with ffmpeg first.
The vocal stem feeds Stage 2; the instrumental is saved for the video (Stage 6).

🇹🇭 Demucs (ผ่าน `python-audio-separator`) แยกเพลงที่อัปโหลดออกเป็น **เสียงร้อง**
ที่สะอาด กับ **ดนตรีล้วน** บางโมเดลให้สองแทร็กตรง ๆ (UVR‑MDX‑NET Karaoke 2) ส่วน
Demucs แบบ 4 แทร็กจะให้ Vocals/Drums/Bass/Other เราจึง **รวมแทร็กที่ไม่ใช่เสียงร้อง**
เข้าด้วยกันเป็นดนตรีล้วน (`_derive_instrumental`) ไฟล์วิดีโอจะถูกดึงเฉพาะเสียงออกมา
ด้วย ffmpeg ก่อน — เสียงร้องส่งต่อไปขั้นที่ 2 ส่วนดนตรีล้วนเก็บไว้ทำวิดีโอในขั้นที่ 6

### Stage 2 — ASR / speech‑to‑text (ถอดเสียงเป็นข้อความ) · `server/app/asr.py`

🇬🇧 `faster-whisper` transcribes the vocal stem into Thai text **segments**, each
with a coarse start/end. Two tricks make it survive sung Thai:
- **Gentler VAD** (voice‑activity detection). Whisper's default VAD is tuned for
  speech and silently drops sustained sung vowels (luk‑thung melisma) — it can
  delete ~95% of a song. We loosen the threshold so singing is kept.
- **Repeat‑loop guard.** Whisper "hallucinates" loops on instrumental breaks
  (e.g. `จื๊ดจื๊ดจื๊ด…` ×60). `collapse_repeats` detects a repeated unit and
  collapses obvious hallucinations **while sparing real choruses** like
  `รักเธอรักเธอ`. The keep/collapse rule lives in one small tunable function.

🇹🇭 `faster-whisper` ถอดเสียงร้องเป็นข้อความภาษาไทยเป็น **ช่วง (segment)** พร้อม
เวลาเริ่ม‑จบแบบหยาบ ๆ มีเทคนิคสองอย่างเพื่อให้ทนกับเสียงร้องไทย:
- **VAD ที่อ่อนลง** (การตรวจจับว่ามีเสียงพูด) ค่าเริ่มต้นของ Whisper ออกแบบมาเพื่อ
  เสียงพูด จึงตัดเสียงร้องเอื้อนยาว ๆ (ลูกทุ่ง) ทิ้งเงียบ ๆ ได้ถึง ~95% ของเพลง
  เราจึงลดความเข้มงวดลงเพื่อเก็บเสียงร้องไว้
- **กันลูปหลอน** Whisper มักสร้างคำซ้ำหลอน ๆ ตอนมีท่อนดนตรี (เช่น `จื๊ดจื๊ดจื๊ด…`
  ×60) ฟังก์ชัน `collapse_repeats` จะตรวจจับหน่วยที่ซ้ำและยุบส่วนที่หลอนชัด ๆ
  **แต่คงท่อนฮุกจริงไว้** เช่น `รักเธอรักเธอ` โดยกฎการเก็บ/ยุบอยู่ในฟังก์ชันเล็ก ๆ
  ที่ปรับค่าได้

### Stage 3 — Forced alignment (จับเวลาระดับตัวอักษร) · `server/app/align.py`

🇬🇧 WhisperX runs a Thai **wav2vec2** model that, given a segment's text *and* its
audio, returns a precise time span for **each character**. WhisperX ships **no
default Thai aligner**, so you must supply one (`ALIGN_MODEL`). If the model is
missing or fails, the stage **degrades gracefully**: it returns no char map and
the next stage interpolates timing instead — the request never crashes. Long sung
lines are split into ≤20 s windows so a single line can't exhaust GPU memory.

🇹🇭 WhisperX เรียกใช้โมเดล **wav2vec2** ภาษาไทย ที่เมื่อได้ข้อความของช่วงหนึ่ง
"พร้อมเสียง" จะคืนเวลาที่แม่นยำของ **แต่ละตัวอักษร** WhisperX **ไม่มีตัวจับเวลา
ภาษาไทยมาให้** จึงต้องกำหนดเองผ่าน `ALIGN_MODEL` หากโมเดลหายไปหรือโหลดล้มเหลว
ขั้นนี้จะ **ลดระดับลงอย่างนุ่มนวล** คือไม่คืนเวลาระดับตัวอักษร แล้วให้ขั้นถัดไป
ประมาณเวลาแทน โดยคำขอจะไม่ล่ม ท่อนร้องยาว ๆ จะถูกซอยเป็นหน้าต่าง ≤20 วินาที
เพื่อไม่ให้ท่อนเดียวกินหน่วยความจำ GPU จนหมด

### Stage 4 — Tokenize & map (ตัดคำไทย + จับคู่เวลา) · `server/app/thai.py`

🇬🇧 PyThaiNLP's **newmm** splits the segment text into real Thai words. Then
`map_words` walks the character‑timing stream to assign a start/end to each word.
Words whose characters don't align are filled by **linear interpolation** between
their matched neighbours, and the result is forced to be **monotonic and
non‑overlapping** (a hard acceptance criterion). With no alignment at all, timing
is spread across the segment proportionally to word length.

🇹🇭 ตัวตัดคำ **newmm** ของ PyThaiNLP แยกข้อความออกเป็นคำไทยจริง ๆ จากนั้น
`map_words` จะไล่ไปตามสายเวลาระดับตัวอักษรเพื่อกำหนดเวลาเริ่ม‑จบให้ทุกคำ คำที่จับคู่
ตัวอักษรไม่ได้จะถูกเติมเวลาด้วย **การประมาณเชิงเส้น** ระหว่างคำข้างเคียงที่จับคู่ได้
และผลลัพธ์จะถูกบังคับให้ **เรียงเวลาไม่ถอยหลังและไม่ทับซ้อนกัน** (เป็นเกณฑ์ที่ต้องผ่าน)
หากไม่มีการจับเวลาเลย จะกระจายเวลาตามความยาวของแต่ละคำในช่วงนั้น

### Stage 5 — Build LRC + ASS (สร้างไฟล์เนื้อเพลง) · `server/app/lrc.py`

🇬🇧 Word spans become two formats. **LRC** has one `[mm:ss.xx]` timestamp per line
— simplest for the web player. **ASS** has a `\k` (karaoke) tag per word in
centiseconds for the color sweep that ffmpeg burns into the video. Long ASR
segments are re‑broken into short, screen‑sized lines (by silence gap, max
duration, or max width). Empty `\k` gaps are inserted for silences so the sweep
clock covers the whole line and never drifts on gappy luk‑thung phrasing.

🇹🇭 ช่วงเวลาของคำถูกแปลงเป็นสองรูปแบบ **LRC** มีหนึ่งเวลา `[mm:ss.xx]` ต่อบรรทัด
ใช้ง่ายที่สุดกับเครื่องเล่นเว็บ ส่วน **ASS** มีแท็ก `\k` (คาราโอเกะ) ต่อหนึ่งคำใน
หน่วยเซนติวินาที สำหรับเอฟเฟกต์ไล่สีที่ ffmpeg เบิร์นลงวิดีโอ ช่วง ASR ที่ยาวจะถูก
ตัดใหม่ให้เป็นบรรทัดสั้นพอดีจอ (ตามช่องเงียบ ความยาวสูงสุด หรือจำนวนตัวอักษร) และมี
การแทรกช่องว่าง `\k` สำหรับช่วงเงียบ เพื่อให้นาฬิกาไล่สีครอบคลุมทั้งบรรทัดและไม่
เพี้ยนกับการวางคำแบบลูกทุ่งที่มีช่องว่างเยอะ

### Stage 6 — Render video (ทำวิดีโอคาราโอเกะ) · `server/app/render.py`

🇬🇧 ffmpeg builds a solid‑color 1280×720 video, burns the ASS subtitles with
**libass**, and muxes in the instrumental audio. A Thai‑capable font is forced
(`RENDER_FONT`, default `Noto Sans Thai`/`Sarabun`) so Thai never renders as tofu
(□□□). It auto‑uses NVENC GPU encoding when available, else CPU `libx264`.

🇹🇭 ffmpeg สร้างวิดีโอพื้นสีล้วน 1280×720 เบิร์นซับ ASS ด้วย **libass** แล้วผสมเสียง
ดนตรีล้วนเข้าไป มีการบังคับใช้ฟอนต์ที่รองรับภาษาไทย (`RENDER_FONT` ค่าเริ่มต้น
`Noto Sans Thai`/`Sarabun`) เพื่อไม่ให้ตัวอักษรไทยกลายเป็นกล่องสี่เหลี่ยม (□□□)
ระบบจะใช้การเข้ารหัสด้วย GPU (NVENC) อัตโนมัติเมื่อมี ไม่เช่นนั้นใช้ CPU (`libx264`)

### Stage 7 — Web player (เครื่องเล่นบนเว็บ) · `web/`

🇬🇧 A static page (no build step) plays the instrumental and reads the word list /
LRC to highlight each word at its timestamp, with an offset slider for fine sync.

🇹🇭 หน้าเว็บแบบ static (ไม่ต้อง build) เล่นดนตรีล้วนและอ่านรายการคำ/LRC เพื่อไฮไลต์
แต่ละคำตามเวลาที่กำหนด พร้อมตัวเลื่อนปรับ offset เพื่อจูนความตรงจังหวะแบบละเอียด

---

## 4. The API / ช่องทางเรียกใช้ (HTTP endpoints)

| Method & path | What it does / หน้าที่ |
|---|---|
| `GET /healthz` | Liveness + current device/model. / เช็คว่าบริการพร้อม + อุปกรณ์/โมเดลที่ใช้ |
| `GET /version` | App version, models, **whether alignment is configured**, last align error. / เวอร์ชัน, โมเดล, สถานะตัวจับเวลา |
| `POST /transcribe` | Vocal wav → LRC/ASS/JSON (Stages 2–5). / เสียงร้อง → ไฟล์เนื้อเพลง |
| `POST /separate` | Full song → zip of `vocals.wav` + `instrumental.wav` (Stage 1). / เพลงเต็ม → แยกเป็นสองแทร็ก |
| `POST /karaoke` | **One‑upload flow:** full song → separate → transcribe in one call (Stages 1–5). / อัปโหลดครั้งเดียวได้ครบ |
| `GET /progress/{id}` | Live stage of a running `/karaoke` job (browser polls this). / สถานะความคืบหน้าของงาน |
| `GET /instrumental/{id}` | Download the instrumental parked by `/karaoke` (one‑time, TTL). / ดาวน์โหลดดนตรีล้วน |
| `POST /render` | Instrumental + ASS → karaoke `.mp4` (Stage 6). / สร้างวิดีโอ |

🇬🇧 **Statelessness & privacy.** Every endpoint works in a temp directory and
deletes it afterward. The `/karaoke` instrumental is the only thing held briefly,
behind an opaque one‑time job id with a TTL sweep. **No user audio is persisted.**

🇹🇭 **ความเป็น stateless และความเป็นส่วนตัว** ทุก endpoint ทำงานในโฟลเดอร์ชั่วคราว
และลบทิ้งหลังเสร็จ มีเพียงดนตรีล้วนของ `/karaoke` ที่ถูกเก็บไว้ชั่วครู่ผ่าน job id
แบบสุ่มใช้ครั้งเดียวและมีตัวกวาดทิ้งตามเวลา **ไม่มีการเก็บไฟล์เสียงของผู้ใช้ถาวร**

---

## 5. Hardware & VRAM discipline / ฮาร์ดแวร์และการจัดการหน่วยความจำ GPU

🇬🇧 The reference machine is a **GTX 1650 with only 4 GB VRAM**, so the pipeline
follows strict rules (it also runs fully on CPU for self‑hosters):
- **Never co‑resident.** Demucs and Whisper must never sit on the GPU at the same
  time. Stages run **sequentially** and each frees its model before the next loads.
- **Quantization.** GPU uses `int8_float16`; CPU uses `int8`. Full fp16 large‑v3
  would not fit in 4 GB.
- **OOM → CPU fallback.** If a stage runs out of GPU memory, it retries once on
  CPU instead of failing the whole song. Separation, ASR, and alignment all do this.
- **Every stage has a working `--device cpu` path** — correctness before speed.

🇹🇭 เครื่องอ้างอิงคือ **GTX 1650 ที่มี VRAM แค่ 4 GB** ระบบจึงมีกฎเข้มงวด (และยัง
รันบน CPU ล้วนได้สำหรับผู้ที่ self‑host):
- **ห้ามอยู่บน GPU พร้อมกัน** Demucs กับ Whisper ต้องไม่อยู่บน GPU พร้อมกันเด็ดขาด
  แต่ละขั้นทำงาน **ตามลำดับ** และคืนหน่วยความจำของโมเดลก่อนที่ขั้นถัดไปจะโหลด
- **การควอนไทซ์** GPU ใช้ `int8_float16` ส่วน CPU ใช้ `int8` เพราะ fp16 เต็ม ๆ
  ของ large‑v3 ใส่ใน 4 GB ไม่พอ
- **OOM แล้วถอยมา CPU** ถ้าขั้นไหนหน่วยความจำ GPU ไม่พอ จะลองใหม่บน CPU หนึ่งครั้ง
  แทนที่จะทำทั้งเพลงล้มเหลว ทั้งการแยกเสียง, ASR และการจับเวลา ทำแบบนี้ทั้งหมด
- **ทุกขั้นมีเส้นทาง `--device cpu` ที่ใช้งานได้จริง** — เน้นความถูกต้องก่อนความเร็ว

---

## 6. Configuration knobs / ตัวแปรปรับแต่ง (environment variables)

| Variable | Default | Purpose / หน้าที่ |
|---|---|---|
| `ASR_MODEL` | `large-v3` | Whisper model; set a Thai‑tuned one (Typhoon / GigaSpeech 2). / เปลี่ยนโมเดล Whisper |
| `ASR_DEVICE` | `auto` | `auto` / `cuda` / `cpu`. |
| `ASR_BEAM_SIZE` | `5` | `1` = greedy/faster, lower accuracy. / `1` เร็วขึ้นแต่แม่นน้อยลง |
| `ALIGN_MODEL` | `airesearch/wav2vec2-large-xlsr-53-th` | Thai aligner; `none` forces interpolation. / โมเดลจับเวลาไทย |
| `ALIGN_DEVICE` | (follows ASR) | Run *just* alignment on CPU to get real timing. / รันเฉพาะการจับเวลาบน CPU |
| `SEPARATION_MODEL` | `htdemucs.yaml` | Demucs/UVR model file. / โมเดลแยกเสียง |
| `SEPARATION_DEVICE` | `cpu` | `cpu` / `cuda` / `auto`. |
| `DEMUCS_SEGMENT` | `7` | Smaller = less VRAM. / เล็กลง = ใช้ VRAM น้อยลง |
| `VAD_FILTER` / `VAD_THRESHOLD` | `true` / `0.2` | Keep sung vowels. / เก็บเสียงร้องเอื้อน |
| `RENDER_FONT` | `Noto Sans Thai` | Thai‑capable subtitle font. / ฟอนต์ซับไตเติลไทย |
| `RENDER_VCODEC` | `libx264` | `h264_nvenc` to GPU‑encode. / ใช้ GPU เข้ารหัสวิดีโอ |
| `MAX_UPLOAD_MB` | `200` | Reject oversized uploads. / จำกัดขนาดไฟล์อัปโหลด |
| `CORS_ORIGINS` | `*` | Restrict who can call the API. / จำกัดต้นทางที่เรียก API ได้ |

---

## 7. Known limitations (be honest) / ข้อจำกัดที่ควรรู้

🇬🇧
- **Luk‑thung is hard.** Heavy melisma/vibrato drifts the ASR; some lines come back
  imperfect. A manual post‑edit step is planned as a fast‑follow.
- **Alignment can silently degrade.** If the Thai aligner can't load, timing falls
  back to interpolation — words still track, but less precisely. `/version`
  surfaces the load error so this is visible.
- **Separating stems does not change a song's copyright.** Process only audio you
  have the right to use. See [`COPYRIGHT_AND_LICENSES.md`](COPYRIGHT_AND_LICENSES.md).

🇹🇭
- **ลูกทุ่งยาก** การเอื้อน/สั่นเสียงมาก ๆ ทำให้ ASR เพี้ยน บางบรรทัดจึงออกมาไม่สมบูรณ์
  มีแผนเพิ่มขั้นตอนแก้ไขด้วยมือ (post‑edit) ตามมาในเร็ว ๆ นี้
- **การจับเวลาอาจลดระดับแบบเงียบ ๆ** ถ้าโหลดตัวจับเวลาไทยไม่ได้ ระบบจะถอยไปใช้การ
  ประมาณเวลาแทน — คำยังตามได้แต่แม่นยำน้อยลง ดูข้อผิดพลาดได้ที่ `/version`
- **การแยกแทร็กไม่ได้เปลี่ยนลิขสิทธิ์ของเพลง** โปรดใช้กับเสียงที่คุณมีสิทธิ์เท่านั้น
  ดูรายละเอียดที่ [`COPYRIGHT_AND_LICENSES.md`](COPYRIGHT_AND_LICENSES.md)

---

## 8. Source map / แผนผังซอร์สโค้ด

```
server/app/
  main.py      ← FastAPI routes, orchestration, temp‑dir I/O, model freeing
  separate.py  ← [1] Demucs separation (vocals + instrumental)
  asr.py       ← [2] faster-whisper + VAD + repeat‑loop guard
  align.py     ← [3] WhisperX wav2vec2 forced alignment (+ graceful degrade)
  thai.py      ← [4] PyThaiNLP tokenize + char→word mapping
  lrc.py       ← [5] build LRC + ASS
  render.py    ← [6] ffmpeg/libass karaoke video
  schemas.py   ← API request/response contract
web/           ← [7] static karaoke player
```

🇬🇧 For deeper notes see the rest of [`docs/`](.) — architecture, performance
tuning, sync accuracy, GPU run guide, and the M0 evaluation notes.

🇹🇭 ดูบันทึกเชิงลึกเพิ่มเติมได้ในโฟลเดอร์ [`docs/`](.) — สถาปัตยกรรม, การจูน
ประสิทธิภาพ, ความแม่นยำของการซิงค์, คู่มือรันบน GPU และบันทึกการประเมินผล M0
