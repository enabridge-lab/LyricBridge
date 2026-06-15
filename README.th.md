# 🎤 LyricBridge

[![English](https://img.shields.io/badge/Lang-English-lightgrey?style=for-the-badge)](README.md) [![ไทย](https://img.shields.io/badge/Lang-ไทย-2ea44f?style=for-the-badge)](README.th.md)

เปลี่ยนเพลงอะไรก็ได้ให้เป็นคาราโอเกะไทยที่ซิงก์ทีละคำ อัปโหลดเพลงเต็ม →
ระบบลบเสียงร้องออก → AI ถอดเนื้อและจับเวลาให้ตรงจังหวะ → ร้องตามในเบราว์เซอร์
โดยแต่ละคำจะสว่างตามบีต หรือจะส่งออกเป็นวิดีโอคาราโอเกะก็ได้

เน้นเพลงไทย/ลูกทุ่งเป็นหลัก รันได้ทั้งบน **GPU** (เร็ว) และ **CPU**
(self-host ไม่ต้องมีการ์ดจอ)

> รีโปนี้เก็บเฉพาะโค้ด ส่วนโมเดล (Thai Whisper, ตัวแยกเสียง, ตัวจับเวลา)
> **จะดาวน์โหลดอัตโนมัติตอนประมวลผลเพลงแรก** แล้วแคชไว้ ดู
> [`docs/REPRODUCIBLE_CLONE.md`](docs/REPRODUCIBLE_CLONE.md)

---

## ▶ รันบน CPU (เครื่องไหนก็ได้ ไม่ต้องมี GPU)

```bash
git clone <your-repo-url> lyricbridge && cd lyricbridge
docker compose up -d        # web :8080 + CPU API :8000
```
เปิด **http://localhost:8080** แล้วลากเพลงใส่ เพลงแรกจะดาวน์โหลดโมเดลครั้งเดียว
(หลังจากนั้นแคชไว้) CPU จะช้ากว่า (คอขวดอยู่ที่การแยกเสียง) แต่ไม่ต้องใช้ GPU
คู่มือ self-host เต็ม ๆ: [`docs/RUN_IT_YOURSELF.md`](docs/RUN_IT_YOURSELF.md)

## ▶ รันบน GPU (NVIDIA / CUDA)

```bash
sudo apt-get install -y ffmpeg fonts-thai-tlwg
./scripts/setup.sh --gpu    # ทำครั้งเดียว: สร้าง server/.venv พร้อม audio-separator[gpu]
./scripts/run_gpu.sh        # web :8080 + GPU API :8000  (ขึ้น device: cuda ✅)
./scripts/stop_gpu.sh       # หยุด
```

> GPU API รันเป็น host process (ทางเดียวที่จะใช้ GTX 1650 บนเครื่องนี้ได้) จึง
> **ไม่** สตาร์ตเองหลังรีบูต — แค่รัน `./scripts/run_gpu.sh` ใหม่ เพลงแรกหลังสตาร์ต
> จะช้า (โหลดโมเดลครั้งเดียว) เพลงถัด ๆ ไปจะเร็ว

---

## กลไกการทำงาน

```
เพลงเต็ม ─▶ แยกเสียง (Demucs) ─▶ ดนตรีล้วน ─▶ 🎵 เครื่องเล่นเว็บ (ไฮไลต์ทีละคำ)
                   └─▶ เสียงร้อง ─▶ ถอดเสียง (Whisper-Thai) ─▶ จับเวลา (wav2vec2-th)
                                       └─▶ ตัดคำ (PyThaiNLP) ─▶ LRC / ASS ─▶ 🎬 วิดีโอ
```

อัปโหลดครั้งเดียว (`POST /karaoke`) รันทั้ง pipeline จบ เบราว์เซอร์แสดงความคืบหน้า
ทีละขั้นแบบเรียลไทม์ (แยกเสียง → ถอดเนื้อ → จับเวลา → สร้างไฟล์)

คำอธิบายแบบละเอียดทีละขั้น (สองภาษา — แต่ละโมเดลทำอะไรและส่งต่อกันยังไง):
[`docs/PIPELINE.md`](docs/PIPELINE.md)

ตั้งโมเดล ASR ภาษาไทยได้ด้วย `ASR_MODEL` (ค่าเริ่มต้นชี้ไปที่ repo บน Hugging Face
และดาวน์โหลดอัตโนมัติ) จะ pin ให้ตรง commit ด้วย `ASR_MODEL_REVISION` เพื่อให้ผลลัพธ์
เหมือนเดิมข้ามเวลาก็ได้ — ดู [`docs/REPRODUCIBLE_CLONE.md`](docs/REPRODUCIBLE_CLONE.md)

| Endpoint | หน้าที่ |
|---|---|
| `POST /karaoke` | เพลงเต็ม → ดนตรี + เนื้อซิงก์คำ ในครั้งเดียว |
| `POST /separate` | เพลง → แทร็กเสียงร้อง + ดนตรี |
| `POST /transcribe` | เสียงร้อง → คำ / LRC / ASS |
| `POST /render` | ดนตรี + ASS → วิดีโอคาราโอเกะ `.mp4` |
| `GET /healthz` `GET /version` | สถานะ (อุปกรณ์ โมเดล เวลา) |

## ความเร็ว

การแยกเสียงกินเวลามากที่สุด เลือกโมเดลได้ด้วย `SEPARATION_MODEL`:

| Model | คุณภาพ | ความเร็ว | หมายเหตุ |
|---|---|---|---|
| `htdemucs.yaml` *(ค่าเริ่มต้น)* | ดี | เร็ว | โมเดลเดียว |
| `htdemucs_ft.yaml` | ดีที่สุด | ช้ากว่า ~4 เท่า | รวม 4 โมเดล (ใช้บน GPU ที่นี่) |
| `UVR_MDXNET_KARA_2.onnx` | เสียงร้องดี | เร็ว | แยก 2 แทร็กในตัว |

บน GTX 1650 คลิป 20 วินาทีรันทั้ง pipeline เสร็จใน ~35 วินาที (เครื่องอุ่นแล้ว)
ส่วนบน CPU แค่การแยกเสียงก็เป็นหลักนาที — การจูนความเร็วและเส้นทาง GPU จึงสำคัญมาก

## การทดสอบ

```bash
cd server && .venv/bin/python -m pytest -q     # backend (54 tests)
cd web && node --test                          # frontend (17 tests)
```

## เอกสาร

- [`docs/PIPELINE.md`](docs/PIPELINE.md) — อธิบาย pipeline + โมเดลแบบ **สองภาษา**
- [`docs/REPRODUCIBLE_CLONE.md`](docs/REPRODUCIBLE_CLONE.md) — โคลนแล้วรัน, เผยแพร่+pin โมเดล, ลิขสิทธิ์
- [`docs/RUN_IT_YOURSELF.md`](docs/RUN_IT_YOURSELF.md) — self-host เต็ม + ตัวแปรทั้งหมด
- [`docs/MODAL_DEPLOYMENT.md`](docs/MODAL_DEPLOYMENT.md) — hosted demo บน Modal · [`docs/RUNBOOK.md`](docs/RUNBOOK.md) — ปฏิบัติการ & rollback
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — การออกแบบระบบ · [`docs/COPYRIGHT_AND_LICENSES.md`](docs/COPYRIGHT_AND_LICENSES.md) — หมายเหตุด้านกฎหมาย
- [`PRD.md`](PRD.md) — สเปกผลิตภัณฑ์ · [`CLAUDE.md`](CLAUDE.md) — ข้อจำกัดการ build

## ลิขสิทธิ์

MIT ต้องให้เครดิต Demucs / UVR ตาม license ของแต่ละโมเดล **การแยกแทร็กเสียงไม่ได้
เปลี่ยนลิขสิทธิ์ของเพลง** — ใช้กับเสียงที่คุณมีสิทธิ์เท่านั้น
