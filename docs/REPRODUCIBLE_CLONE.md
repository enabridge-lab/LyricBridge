# 🔁 Reproducible from a fresh clone / โคลนแล้วรันได้เหมือนเดิม

> **Goal / เป้าหมาย:** `git clone` → run, and get **the same behavior on GPU and
> CPU** as the original machine — *over time*, not just today.
>
> โคลนแล้วรันให้ได้ **พฤติกรรมเหมือนเครื่องต้นฉบับทั้งบน GPU และ CPU** และต้อง
> เหมือนเดิม **ข้ามกาลเวลา** ไม่ใช่แค่วันนี้

---

## 1. What the repo does and does NOT ship / สิ่งที่อยู่และไม่อยู่ในรีโป

🇬🇧 The repo ships **code + config only**. Large models are **not** committed —
they are re-fetched on first run and cached. This keeps the clone small and
avoids GitHub's 100 MB/file limit and copyright issues.

🇹🇭 รีโปเก็บ **เฉพาะโค้ดและคอนฟิก** โมเดลขนาดใหญ่ **ไม่ถูก commit** แต่จะถูกดึงมา
ใหม่ตอนรันครั้งแรกแล้วแคชไว้ ทำให้โคลนเล็ก เลี่ยงลิมิต 100 MB/ไฟล์ของ GitHub และ
ปัญหาลิขสิทธิ์

| Component | Shipped in repo? | How a clone gets it / โคลนได้มาอย่างไร |
|---|---|---|
| App code, scripts, docs | ✅ yes | in the repo |
| Thai Whisper (ASR) `~2.9 GB` | ❌ no | **Hugging Face Hub** (auto-download, pinned) |
| Demucs / separator weights | ❌ no | auto-download on first separation |
| wav2vec2 Thai aligner | ❌ no | auto-download from HF (`ALIGN_MODEL`) |
| `server/.venv` (GPU host) | ❌ no | `./scripts/setup.sh --gpu` |
| Sample songs / vocal stems | ❌ no | copyrighted — bring your own audio |

---

## 2. Quick start for a cloner / เริ่มใช้งานหลังโคลน

### CPU (any machine, no GPU) / รันบน CPU

```bash
git clone <your-repo-url> lyricbridge && cd lyricbridge
docker compose up -d            # web :8080 + CPU API :8000
# First song downloads the models once, then it's cached. Open localhost:8080.
```

### GPU (NVIDIA/CUDA) / รันบน GPU

```bash
git clone <your-repo-url> lyricbridge && cd lyricbridge
sudo apt-get install -y ffmpeg fonts-thai-tlwg
./scripts/setup.sh --gpu        # builds server/.venv with audio-separator[gpu]
./scripts/run_gpu.sh            # GPU API :8000 + web :8080
```

🇬🇧 Verify it's reproducible: `curl localhost:8000/version` shows `asr_model`,
`asr_model_revision` (the pinned commit), and `separation_model`.

🇹🇭 ตรวจว่าทำซ้ำได้: `curl localhost:8000/version` จะแสดง `asr_model`,
`asr_model_revision` (commit ที่ pin ไว้) และ `separation_model`

---

## 3. Owner one-time setup: publish + PIN the Thai model / เจ้าของทำครั้งเดียว

🇬🇧 "Runs exactly the same" depends on the Thai Whisper model. Publish it once to
HF, then **pin a commit hash** so a future re-upload can't silently change old
clones' output.

🇹🇭 "รันได้เหมือนเดิม" ขึ้นกับโมเดล Whisper ภาษาไทย ให้เผยแพร่ขึ้น HF หนึ่งครั้ง
แล้ว **pin ด้วย commit hash** เพื่อกันไม่ให้การอัปโหลดใหม่ในอนาคตเปลี่ยน output
ของโคลนเก่าแบบเงียบ ๆ

```bash
pip install -U "huggingface_hub[cli]"
hf auth login

# Upload the converted CT2 model directory (one repo, one time).
hf upload champkrap/whisper-th-large-v3-ct2 ./models/whisper-th-large-v3-ct2 . \
    --repo-type model

# Get the commit hash to PIN to (the latest revision on main):
hf api repos/champkrap/whisper-th-large-v3-ct2/revision/main | grep -m1 '"oid"'
#   or read it from the model's "Files and versions" page on huggingface.co
```

🇬🇧 Then set the pin everywhere a clone reads it:

🇹🇭 จากนั้นตั้งค่า pin ในทุกที่ที่โคลนอ่านค่า:

- **`docker-compose.yml`** → `ASR_MODEL_REVISION: ${ASR_MODEL_REVISION:-<commit-hash>}`
- **GPU runs** → `ASR_MODEL_REVISION=<commit-hash> ./scripts/run_gpu.sh`
- Or commit a `.env` line `ASR_MODEL_REVISION=<commit-hash>` (compose reads it).

> ⚠️ With an **empty** `ASR_MODEL_REVISION`, a clone loads the *latest* model — it
> works, but is **not** reproducible if you ever re-upload. Pinning the hash is
> what makes it stable across time. / ถ้าเว้นว่างจะโหลดตัวล่าสุด — รันได้แต่ไม่
> reproducible ถ้าอัปโหลดใหม่ การ pin hash คือสิ่งที่ทำให้คงที่ข้ามเวลา

---

## 4. License & credit (REQUIRED) / ลิขสิทธิ์และเครดิต (จำเป็น)

🇬🇧 Your CT2 model was **converted from an upstream Thai Whisper** —
per [`M0_EVAL_NOTES.md`](M0_EVAL_NOTES.md) the source is
[`biodatlab/whisper-th-large-v3-combined`](https://huggingface.co/biodatlab/whisper-th-large-v3-combined).
Before redistributing it on HF you must:
1. ✅ **Source identified:** `biodatlab/whisper-th-large-v3-combined` (confirm this
   is the one you actually converted).
2. **Check its license allows redistribution** (and any required attribution) by
   reading the license field on that model's HF page. **Do not assume** — verify
   the exact terms before re-uploading.
3. **Credit it in your HF model card** and in this repo. `CLAUDE.md` requires
   crediting model licenses; the project itself is MIT (see `LICENSE`).

🇹🇭 โมเดล CT2 ของคุณ **convert มาจาก Whisper ภาษาไทยตัวต้นทาง** —
ตาม [`M0_EVAL_NOTES.md`](M0_EVAL_NOTES.md) ต้นทางคือ
[`biodatlab/whisper-th-large-v3-combined`](https://huggingface.co/biodatlab/whisper-th-large-v3-combined)
ก่อนนำขึ้น HF เพื่อแจกจ่าย ต้อง:
1. ✅ **ระบุต้นทางแล้ว:** `biodatlab/whisper-th-large-v3-combined` (ยืนยันว่าเป็น
   ตัวที่คุณ convert จริง)
2. **ตรวจว่า license อนุญาตให้ redistribute** (และต้องให้เครดิตแบบไหน) โดยอ่านช่อง
   license บนหน้าโมเดลนั้นใน HF — **อย่าเดา** ต้องยืนยันเงื่อนไขจริงก่อนอัปโหลดซ้ำ
3. **ใส่เครดิตไว้ในการ์ดโมเดลบน HF** และในรีโปนี้ — `CLAUDE.md` กำหนดให้ต้อง
   เครดิต license ของโมเดล ส่วนตัวโปรเจกต์เป็น MIT (ดู `LICENSE`)

> The CT2 conversion does not change the upstream license — the original terms
> still apply. / การแปลงเป็น CT2 ไม่เปลี่ยน license ต้นทาง เงื่อนไขเดิมยังมีผล

### HF model card template / เทมเพลตการ์ดโมเดล (`README.md` on the HF repo)

```markdown
---
license: <UPSTREAM_LICENSE>        # MUST match biodatlab/whisper-th-large-v3-combined
language: th
library_name: ctranslate2
base_model: biodatlab/whisper-th-large-v3-combined
tags: [whisper, faster-whisper, ctranslate2, thai, asr]
---

# whisper-th-large-v3-ct2

CTranslate2 (int8_float16/int8) conversion of **biodatlab/whisper-th-large-v3-combined**
for use with faster-whisper, tuned for Thai / luk-thung karaoke transcription.

## Credit & license
Converted from [biodatlab/whisper-th-large-v3-combined](https://huggingface.co/biodatlab/whisper-th-large-v3-combined),
licensed under **<UPSTREAM_LICENSE>** (fill in from that model's page). This
conversion is redistributed under the same terms. Demucs / UVR weights used by the
pipeline are credited per their own model licenses.

## Usage
```python
from faster_whisper import WhisperModel
m = WhisperModel("champkrap/whisper-th-large-v3-ct2",
                 revision="<COMMIT_HASH>", device="cuda", compute_type="int8_float16")
```
```

---

## 5. Pre-publish checklist / เช็กลิสต์ก่อนอัปโหลด GitHub

- [ ] `.gitignore` excludes `models/`, `*.wav/mp3/mp4`, `samples*/`, `.venv/`,
      `.omc/`, caches, `docker-compose.override.yml`, `.claude/settings.local.json`.
- [ ] `git status` shows **no** multi‑GB files and **no** song audio staged.
- [ ] Thai model published to HF + `ASR_MODEL_REVISION` pinned to a commit hash.
- [ ] HF model card credits the upstream model + its license.
- [ ] `docker compose up` on a clean clone reaches `localhost:8080` and processes
      a song (models auto-download once).
- [ ] No secrets/tokens committed (the service needs none).
