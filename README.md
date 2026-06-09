# 🎤 LyricBridge

🇬🇧 Turn any song into word-synced Thai karaoke. Upload a full song → the vocals
are removed → AI transcribes and time-aligns the lyrics → sing along in the
browser with each word lighting up on beat, or export a karaoke video.

🇹🇭 เปลี่ยนเพลงอะไรก็ได้ให้เป็นคาราโอเกะไทยที่ซิงก์ทีละคำ อัปโหลดเพลงเต็ม →
ระบบลบเสียงร้องออก → AI ถอดเนื้อและจับเวลาให้ตรงจังหวะ → ร้องตามในเบราว์เซอร์
โดยแต่ละคำจะสว่างตามบีต หรือจะส่งออกเป็นวิดีโอคาราโอเกะก็ได้

🇬🇧 Thai / luk-thung is the priority. Runs on **GPU** (fast) or **CPU**
(self-host, no GPU needed).
🇹🇭 เน้นเพลงไทย/ลูกทุ่งเป็นหลัก รันได้ทั้งบน **GPU** (เร็ว) และ **CPU**
(self-host ไม่ต้องมีการ์ดจอ)

> 🇬🇧 The repo ships code only — the models (Thai Whisper, separator, aligner)
> **auto-download on the first song** and are cached.
> 🇹🇭 รีโปนี้เก็บเฉพาะโค้ด ส่วนโมเดล (Thai Whisper, ตัวแยกเสียง, ตัวจับเวลา)
> **จะดาวน์โหลดอัตโนมัติตอนประมวลผลเพลงแรก** แล้วแคชไว้
> → [`docs/REPRODUCIBLE_CLONE.md`](docs/REPRODUCIBLE_CLONE.md)

---

## ▶ Run on CPU — รันบน CPU (any machine, no GPU / เครื่องไหนก็ได้ ไม่ต้องมี GPU)

```bash
git clone <your-repo-url> lyricbridge && cd lyricbridge
docker compose up -d        # web :8080 + CPU API :8000
```

🇬🇧 Open **http://localhost:8080** and drop in a song. The first song downloads
the models once (then it's cached); CPU is slower (separation is the bottleneck)
but needs no GPU. Full self-host guide: [`docs/RUN_IT_YOURSELF.md`](docs/RUN_IT_YOURSELF.md).

🇹🇭 เปิด **http://localhost:8080** แล้วลากเพลงใส่ เพลงแรกจะดาวน์โหลดโมเดลครั้งเดียว
(หลังจากนั้นแคชไว้) CPU จะช้ากว่า (คอขวดอยู่ที่การแยกเสียง) แต่ไม่ต้องใช้ GPU
คู่มือ self-host เต็ม ๆ: [`docs/RUN_IT_YOURSELF.md`](docs/RUN_IT_YOURSELF.md)

## ▶ Run on GPU — รันบน GPU (NVIDIA / CUDA)

```bash
sudo apt-get install -y ffmpeg fonts-thai-tlwg
./scripts/setup.sh --gpu    # one-time: build server/.venv with audio-separator[gpu]
./scripts/run_gpu.sh        # web :8080 + GPU API :8000  (prints device: cuda ✅)
./scripts/stop_gpu.sh       # stop
```

> 🇬🇧 The GPU API runs as a host process (the only way to reach the GTX 1650
> here), so it does **not** auto-start on reboot — just run `./scripts/run_gpu.sh`
> again. First song after starting is slower (models load once); later songs are fast.
>
> 🇹🇭 GPU API รันเป็น host process (ทางเดียวที่จะใช้ GTX 1650 บนเครื่องนี้ได้)
> จึง **ไม่** สตาร์ตเองหลังรีบูต — แค่รัน `./scripts/run_gpu.sh` ใหม่ เพลงแรกหลัง
> สตาร์ตจะช้า (โหลดโมเดลครั้งเดียว) เพลงถัด ๆ ไปจะเร็ว

---

## How it works / กลไกการทำงาน

```
full song ─▶ separate (Demucs)  ─▶ instrumental ─▶ 🎵 web player (word highlight)
                   └─▶ vocals ─▶ transcribe (Whisper-Thai) ─▶ align (wav2vec2-th)
                                       └─▶ tokenize (PyThaiNLP) ─▶ LRC / ASS ─▶ 🎬 video
```

🇬🇧 One upload (`POST /karaoke`) runs the whole pipeline; the browser shows live
per-stage progress (แยกเสียง → ถอดเนื้อ → จับเวลา → สร้างไฟล์).
🇹🇭 อัปโหลดครั้งเดียว (`POST /karaoke`) รันทั้ง pipeline จบ เบราว์เซอร์แสดง
ความคืบหน้าทีละขั้นแบบเรียลไทม์ (แยกเสียง → ถอดเนื้อ → จับเวลา → สร้างไฟล์)

🇬🇧 Full stage-by-stage walkthrough (bilingual — every model, what it does, and
how it hands off): [`docs/PIPELINE.md`](docs/PIPELINE.md).
🇹🇭 คำอธิบายแบบละเอียดทีละขั้น (สองภาษา — แต่ละโมเดลทำอะไรและส่งต่อกันยังไง):
[`docs/PIPELINE.md`](docs/PIPELINE.md)

🇬🇧 The Thai-tuned ASR model is set with `ASR_MODEL` (defaults to a Hugging Face
repo, auto-downloaded). Pin it to a commit with `ASR_MODEL_REVISION` for output
that stays identical over time — see [`docs/REPRODUCIBLE_CLONE.md`](docs/REPRODUCIBLE_CLONE.md).
🇹🇭 ตั้งโมเดล ASR ภาษาไทยได้ด้วย `ASR_MODEL` (ค่าเริ่มต้นชี้ไปที่ repo บน Hugging
Face และดาวน์โหลดอัตโนมัติ) จะ pin ให้ตรง commit ด้วย `ASR_MODEL_REVISION` เพื่อให้
ผลลัพธ์เหมือนเดิมข้ามเวลาก็ได้ — ดู [`docs/REPRODUCIBLE_CLONE.md`](docs/REPRODUCIBLE_CLONE.md)

| Endpoint | Does / หน้าที่ |
|---|---|
| `POST /karaoke` | full song → instrumental + word-timed lyrics (one call) / เพลงเต็ม → ดนตรี + เนื้อซิงก์คำ ในครั้งเดียว |
| `POST /separate` | song → vocals + instrumental stems / เพลง → แทร็กเสียงร้อง + ดนตรี |
| `POST /transcribe` | vocal stem → words / LRC / ASS / เสียงร้อง → คำ / LRC / ASS |
| `POST /render` | instrumental + ASS → burned karaoke `.mp4` / ดนตรี + ASS → วิดีโอคาราโอเกะ |
| `GET /healthz` `GET /version` | status (device, models, timings) / สถานะ (อุปกรณ์ โมเดล เวลา) |

## Speed / ความเร็ว

🇬🇧 Separation dominates the time. Pick a model with `SEPARATION_MODEL`:
🇹🇭 การแยกเสียงกินเวลามากที่สุด เลือกโมเดลได้ด้วย `SEPARATION_MODEL`:

| Model | Quality / คุณภาพ | Speed / ความเร็ว | Notes / หมายเหตุ |
|---|---|---|---|
| `htdemucs.yaml` *(default)* | good / ดี | fast / เร็ว | single Demucs model / โมเดลเดียว |
| `htdemucs_ft.yaml` | best / ดีที่สุด | ~4× slower / ช้ากว่า ~4 เท่า | 4-model ensemble (used on GPU here) / รวม 4 โมเดล |
| `UVR_MDXNET_KARA_2.onnx` | good vocals / เสียงร้องดี | fast / เร็ว | native 2-stem / แยก 2 แทร็กในตัว |

🇬🇧 On the GTX 1650 a 20 s clip runs the whole pipeline in ~35 s (warm). On CPU,
separation alone is minutes — so the speed tuning + GPU path matter a lot.
🇹🇭 บน GTX 1650 คลิป 20 วินาทีรันทั้ง pipeline เสร็จใน ~35 วินาที (เครื่องอุ่นแล้ว)
ส่วนบน CPU แค่การแยกเสียงก็เป็นหลักนาที — การจูนความเร็วและเส้นทาง GPU จึงสำคัญมาก

## Tests / การทดสอบ

```bash
cd server && .venv/bin/python -m pytest -q     # backend (54 tests)
cd web && node --test                          # frontend (17 tests)
```

## Docs / เอกสาร

- [`docs/PIPELINE.md`](docs/PIPELINE.md) — **bilingual** full pipeline + models explainer / อธิบาย pipeline + โมเดลแบบสองภาษา
- [`docs/REPRODUCIBLE_CLONE.md`](docs/REPRODUCIBLE_CLONE.md) — clone→run, publish + pin the model, license / โคลนแล้วรัน, เผยแพร่+pin โมเดล, ลิขสิทธิ์
- [`docs/RUN_IT_YOURSELF.md`](docs/RUN_IT_YOURSELF.md) — full self-host + all env vars / self-host เต็ม + ตัวแปรทั้งหมด
- [`docs/PERFORMANCE_TUNING.md`](docs/PERFORMANCE_TUNING.md) — speed tuning spec / สเปกการจูนความเร็ว
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — system design / การออกแบบระบบ · [`docs/COPYRIGHT_AND_LICENSES.md`](docs/COPYRIGHT_AND_LICENSES.md) — legal notes / หมายเหตุด้านกฎหมาย
- [`PRD.md`](PRD.md) — product spec / สเปกผลิตภัณฑ์ · [`CLAUDE.md`](CLAUDE.md) — build constraints / ข้อจำกัดการ build

## License / ลิขสิทธิ์

🇬🇧 MIT. Credit Demucs / UVR per their model licenses. **Separating stems does
not change a song's copyright** — only process audio you have the right to use.

🇹🇭 MIT ต้องให้เครดิต Demucs / UVR ตาม license ของแต่ละโมเดล **การแยกแทร็กเสียง
ไม่ได้เปลี่ยนลิขสิทธิ์ของเพลง** — ใช้กับเสียงที่คุณมีสิทธิ์เท่านั้น
