# LyricBridge — Project Brief (Handoff)

> Original Thai handoff brief. **`PRD.md` supersedes this for build details** but this captures the
> reasoning and decisions verbatim.

เอกสารนี้สรุปทุกการตัดสินใจ + research สำหรับส่งต่อให้ทีม/agent ทำต่อ อ่านจบแล้วเริ่ม build ได้เลยโดยไม่ต้องมี context เพิ่ม

---

## 1. เป้าหมาย
สร้าง **LyricBridge แบบ open-source** ที่ใครก็ download ไปรันเองได้ จุดต่างหลัก:
- **อัปเพลงอะไรก็ได้** — user อัปไฟล์เพลงเอง (.mp4 / .mp3) ไม่ต้องรอคลังเพลง
- **ลบเสียงร้อง** → ได้ backing track ไว้ร้องตาม
- **โชว์เนื้อเพลงวิ่งตามจังหวะ** (word-level sync)
- **เน้นเพลงไทย / ลูกทุ่ง** — เป็นทั้งจุดขายและจุดยากที่สุด

---

## 2. การตัดสินใจที่ fix แล้ว (ห้ามเปลี่ยนโดยไม่คุยกับเจ้าของโปรเจกต์)

| หัวข้อ | ตัดสินใจ | เหตุผล / ผลกระทบ |
|---|---|---|
| **รันที่ไหน** | Hybrid — แยกเสียง **on-device**, ถอด+sync บน **cloud** | งานหนัก (แยกเสียง) ไปอยู่ที่เครื่อง user → server ถูกมาก + scale ฟรี และได้ privacy ฟรี (ดนตรีไม่ออกจากเครื่อง ส่งแค่เสียงร้องขึ้น cloud) |
| **เนื้อเพลงมาจากไหน** | **ASR ถอดเองล้วน** (ไม่ดึงเนื้อ online, ไม่บังคับ user วาง) | auto สุด แต่ **ยอมรับว่าเพี้ยนกับลูกทุ่ง** — ต้องวางแผน path แก้ทีหลัง (ดูข้อ 5 + 8) |
| **Output (MVP)** | (1) เล่นในเว็บ + เนื้อวิ่ง real-time, (2) render เป็นวิดีโอคาราโอเกะ | ทำทั้งสองอย่าง |
| **License** | Open-source (MIT แนะนำ) | ต้องให้เครดิต UVR/Demucs ตาม license ของ model ที่ใช้ |

---

## 3. สถาปัตยกรรม (pipeline)

```
[เครื่อง user]                          [cloud]                      [เครื่อง user]
อัปโหลดเพลง → แยกเสียง (on-device) → ┬─ Instrumental (อยู่บนเครื่อง) ──────────────┐
                                     └─ Vocals ──→ Thai ASR → ตัดคำ+align → LRC/ASS ┘
                                                                                    ↓
                                                          เล่น (web player) + render วิดีโอ
```

**จุดสำคัญ:** cloud รับแค่ vocal stem → ASR ทำงานบนเสียงร้องที่สะอาดแล้ว (แม่นขึ้น) + bandwidth/privacy ดีขึ้น instrumental ไม่เคยออกจากเครื่อง สุดท้ายมารวมกับไฟล์ timing ที่ player

---

## 4. Tech stack แยกตามก้อน

### ก้อน 1 — แยกเสียง (บนเครื่อง)

| Model | คุณภาพ | ความเร็ว / ฮาร์ดแวร์ | ใช้เมื่อ |
|---|---|---|---|
| **HTDemucs v4 (ft)** | ดีสุดในกลุ่ม free (~3 dB เหนือ Spleeter) | ~20–30s/เพลง, GPU 4–8GB VRAM | default คุณภาพสูง |
| **UVR-MDX-NET Karaoke 2** | ทำมาเพื่อคาราโอเกะ — ลบเสียงร้องนำ แต่เก็บคอรัส/backing vocals | ผ่าน onnxruntime | option "เก็บคอรัสไว้ร้องตาม" |
| **Spleeter** | พอใช้ มี artifact | เร็วมาก รันบน CPU/RAM 8GB ได้ | fallback เครื่องไม่มี GPU |

- **วิธีรัน on-device ในเว็บ:** MDX-Net เป็น ONNX → รันผ่าน `onnxruntime-web` + **WebGPU** ในเบราว์เซอร์ (ดู §7 ความเสี่ยง)
- **ทางเลือกที่ปลอดภัยกว่า:** ห่อเป็น desktop app (Tauri/Electron) bundle Demucs ไว้ในเครื่อง
- **อย่าเขียน wrapper เอง:** ใช้ [`python-audio-separator`](https://github.com/nomadkaraoke/python-audio-separator) (MIT) — ห่อ MDX-Net / VR Arch / Demucs / MDXC จาก UVR ให้เรียกผ่าน Python/CLI ได้ ใช้เป็น reference หรือใช้ตรงๆ ในโหมด server-side ตอน prototype
- แตก audio จาก .mp4 ด้วย **ffmpeg** (หรือ `ffmpeg.wasm` ฝั่ง browser)

### ก้อน 2 — ถอดเนื้อ + จับเวลา (บน cloud)

| งาน | เครื่องมือแนะนำ | หมายเหตุ |
|---|---|---|
| Thai ASR | **Typhoon ASR** (SCB 10X, ทีมไทย) หรือ Whisper large-v3 + ไทย | Whisper เปล่าๆ อ่อนกับไทย — Typhoon ถูกกว่า ~45× แต่ความแม่นใกล้กัน; model สาย GigaSpeech 2 ลด WER ไทยได้ 25–40% เทียบ Whisper large-v3 บนเสียง YouTube จริง |
| Word-level timestamp | **WhisperX** (forced alignment ด้วย wav2vec2) | ได้ timing ระดับคำ; MFA แม่นกว่าแต่ต้องมี pronunciation dictionary ไทย (งานเพิ่ม) |
| ตัดคำไทย | **PyThaiNLP** (newmm / deepcut) | **จำเป็น** — ภาษาไทยไม่มีช่องว่างระหว่างคำ ถ้าไม่ตัดคำจะ highlight ได้แค่ทีละบรรทัด ไม่ใช่ทีละคำ |
| รัน ASR ให้เบา | **faster-whisper** | รันบน CPU ได้ ทำให้ server ถูก |

### ก้อน 3 — เล่น + render (บนเครื่อง)

| งาน | เครื่องมือ | หมายเหตุ |
|---|---|---|
| ไฟล์ timing | **LRC** (บรรทัด) + **ASS** (มี `\k` tag = คาราโอเกะแบบไล่สีทีละคำ) | LRC ง่ายสุด, ASS ไว้ทำ effect |
| Web player | HTML/JS ไฮไลต์ทีละคำตาม timestamp | real-time scrolling |
| Render วิดีโอ | **ffmpeg** burn ASS subtitle ทับ instrumental | ได้ไฟล์วิดีโอคาราโอเกะ |

---

## 5. มุมเพลงไทย — ความรู้ที่ห้ามลืม (จุดแพ้/ชนะ)

ปัญหา 3 อย่างที่โปรเจกต์ฝรั่งทั่วไป **ไม่ได้แก้ให้** ต้องทำเอง:

1. **ไม่มีช่องว่างระหว่างคำ** → ต้อง PyThaiNLP ตัดคำก่อนทำ word-level highlight
2. **Whisper อ่อนกับไทย** → ใช้ Thai-tuned ASR (Typhoon / GigaSpeech 2) ไม่ใช่ Whisper เปล่า
3. **เสียงร้องลูกทุ่งยากกว่าเสียงพูดมาก** (เอื้อน, vibrato, ลากเสียง) → ASR auto ล้วน **จะเพี้ยน** การแยกเสียงก่อน (vocal stem สะอาด) ช่วยได้ระดับหนึ่ง แต่ต้องวางแผน path ให้ user แก้เนื้อ/timing ทีหลัง (แม้ default flow จะเป็น auto)

> **คำเตือนสำหรับคนทำต่อ:** เลือก "ASR ถอดเองล้วน" ไว้แล้ว แปลว่า MVP จะออกมา "พอใช้แต่ไม่เป๊ะ" กับลูกทุ่ง อย่าเซอร์ไพรส์ ให้เตรียมช่องแก้ไข (post-edit) เป็น fast-follow

---

## 6. โปรเจกต์อ้างอิง (ศึกษา/ต่อยอด อย่าเริ่มจากศูนย์)

| โปรเจกต์ | เอาอะไรไปใช้ |
|---|---|
| **nomadkaraoke** (`karaoke-gen` + `python-audio-separator` + `python-lyrics-transcriber`) | สถาปัตยกรรมที่ mature สุด, MIT — มี anchor sequence + LLM auto-correct เนื้อ, export ASS/LRC/CDG/video, มี review UI ใช้เป็นแม่แบบหลัก |
| **OpenKara** (thedavidweng) | ปรัชญา "เปลี่ยนคลังเพลงที่มีอยู่เป็นคาราโอเกะ" + on-device separation |
| **karaok-AI** (EtienneAb3d) | ตัว editor ให้คนแก้ timing เอง (สำคัญมากกับเพลงไทย) |
| **KarAIoke** (dylanbliss) | ไอเดีย UX: ลูกบอลเด้ง, generative art พื้นหลัง (ระวังอย่าให้ scope บวมเหมือนเขา) |

---

## 7. โครงสร้าง repo ที่เสนอ (monorepo)

```
/web        Frontend: อัปโหลด, แยกเสียง on-device (onnxruntime-web/WebGPU), player, render
/server     FastAPI service: vocal wav เข้า → LRC/ASS ออก (faster-whisper + WhisperX + PyThaiNLP)
            + Dockerfile
/models     สคริปต์ download model (Demucs/MDX ONNX, Typhoon, wav2vec2 ไทย)
docker-compose.yml
/docs       "วิธีรันเอง" + สถาปัตยกรรม
```

**"ใครก็รันได้" =** cloud part เป็น Docker image เดียว (รัน CPU ได้ด้วย faster-whisper), frontend เป็น static site เพราะงานแยกเสียงอยู่ฝั่ง client → host แทบไม่มีต้นทุน GPU แจก `docker compose up` + static build ก็จบ

> หมายเหตุ deploy: ถ้าอยากมี public demo เจ้าของโปรเจกต์มี domain `avocadu14.com` อยู่แล้ว ใช้ host ตัว demo/frontend ได้

---

## 8. ลำดับ build (milestones)

| Milestone | ทำอะไร | เกณฑ์ผ่าน |
|---|---|---|
| **M0 — Cloud ASR service** | FastAPI: รับ vocal .wav → คืน LRC (Typhoon/faster-whisper + WhisperX + PyThaiNLP) | ทดสอบกับเพลงลูกทุ่งจริง 5 เพลง ดูว่าเนื้อ+timing พอใช้ไหม **(validate ความเสี่ยงไทยตั้งแต่ต้น)** |
| **M1 — Separation** | เริ่ม **server-side Demucs** ให้ pipeline จบ end-to-end ก่อน แล้วค่อยย้ายไป **on-device (WebGPU/ONNX)** | อัปเพลง → ได้ instrumental + vocals |
| **M2 — Web player** | เล่น instrumental + ไฮไลต์เนื้อทีละคำตาม LRC real-time | เนื้อวิ่งตรงจังหวะในเบราว์เซอร์ |
| **M3 — Video render** | ffmpeg + ASS burn-in | ได้ไฟล์ .mp4 คาราโอเกะ |
| **M4 — Polish & ship** | post-edit เนื้อ/timing (option), docker-compose, docs "run it yourself" | คนนอก clone แล้วรันเองได้ |

---

## 9. ความเสี่ยง / ของที่ต้อง validate

1. **แยกเสียงในเบราว์เซอร์ด้วย WebGPU เป็นส่วนใหม่/เสี่ยงสุด** — perf/VRAM บนมือถืออาจไม่ไหว
   *Fallback:* desktop runner (bundle Demucs) หรือย้าย separation ขึ้น cloud ชั่วคราว
2. **ASR auto กับลูกทุ่งจะไม่เป๊ะ** — ตั้งความคาดหวัง + เตรียม post-edit (M4)
3. **Forced alignment กับเสียงร้องไทย** — อาจต้องหา/เทรน wav2vec2 alignment model ไทย ทดสอบใน M0
4. **ลิขสิทธิ์:** ตัว tool/model (MIT) ใช้ได้ แต่ตัวเพลงเป็นของ user — การแยก stem ไม่เปลี่ยนสถานะลิขสิทธิ์เพลง ใส่หมายเหตุนี้ใน docs

---

## 10. TL;DR สำหรับคนทำต่อ

เริ่มที่ **M0 (cloud ASR service)** ก่อนเสมอ เพราะมันคือจุดที่จะรู้เร็วที่สุดว่าไอเดีย "เพลงไทย/ลูกทุ่ง auto" เวิร์กแค่ไหน ใช้ Thai-tuned ASR + PyThaiNLP + WhisperX อย่าใช้ Whisper เปล่า แยกเสียงเริ่มที่ server-side Demucs ให้จบ pipeline แล้วค่อยดัน on-device ทีหลัง ต่อยอดจาก nomadkaraoke แทนการเขียนใหม่ทั้งหมด
