# MODAL DEPLOYMENT — LyricBridge hosted demo (design + cost + decision record)

> **Status: ✅ SHIPPED.** Backend live (Modal app `lyricbridge`, region `ap`), models on
> Volume, end-to-end karaoke + video export verified. This doc is the **as-built record**:
> the architecture, the Modal-imposed design constraints, the cost model, and the
> owner decisions (A2). Operations & rollback live in `docs/RUNBOOK.md`; how to write
> Modal code lives in `docs/MODAL_RULES.md`.
>
> The Dx/Px sections below are kept as the **build history** (what was done, with the
> acceptance gate each step passed) — useful when extending or re-deploying, not a TODO.
> Modal API ref: modal.com/docs · machine-readable: modal.com/llms-full.txt

---

## ⚠️ หมายเหตุ locked decision (อ่านก่อน)

PRD §2 ล็อกไว้ว่า "cloud ได้รับเฉพาะ vocal stem" — แต่ deploy นี้ทั้ง pipeline
(รวม separation) อยู่บน Modal = **เพลงเต็มถูกอัปโหลดขึ้น cloud**.
เจ้าของรับทราบและอนุมัติแล้วสำหรับ **hosted demo/production สาธารณะ** (self-host path เดิมไม่เปลี่ยน).
ข้อ "ไม่ persist user audio" ยังต้องถือเสมอ: ทุกอย่างอยู่ใน temp/TTL store, sweep อัตโนมัติ.

---

# Part A — สิ่งที่ต้องได้จากเจ้าของก่อนเริ่ม (Claude Code ทำแทนไม่ได้)

> Claude Code: ถ้ารายการ Required ข้อไหนยังไม่มี ให้หยุดและขอจากเจ้าของก่อน อย่าเดา/อย่า mock.

## A1. Accounts ที่ต้องมี (Required)

| # | Account | ใช้ทำอะไร | แผน/ราคา | สิ่งที่ต้องส่งมอบให้ Claude Code |
|---|---|---|---|---|
| 1 | **Modal** (modal.com) | รัน backend GPU | Starter ฟรี ($30 เครดิต/เดือน) | ✅ **เสร็จ** — `modal setup` login สำเร็จ, workspace = **`chkrap47`** (token ใน `~/.modal.toml`, ไม่ commit). Backend URL จะเป็น `https://chkrap47--lyricbridge-web.modal.run` |
| 2 | **GitHub** | repo + CI/CD + (option) Pages | ฟรี | ✅ **เสร็จ** — repo `enabridge-lab/LyricBridge` อยู่บน GitHub แล้ว (Actions secrets ตั้งตอน P1) |
| 3 | **Cloudflare** (แนะนำ) หรือใช้ GitHub Pages แทน | โฮสต์ frontend static | ฟรี | ✅ **เลือก GitHub Pages** (source = GitHub Actions, publish `web/`) |

## A2. ข้อมูล/การตัดสินใจที่ต้องตอบ (Required — ใส่คำตอบลงตารางนี้เลย)

| คำถาม | ตัวเลือก | คำตอบของเจ้าของ |
|---|---|---|
| โดเมน frontend | a) `*.pages.dev` / `*.github.io` ฟรี b) โดเมนส่วนตัว (ต้องมีโดเมน + ชี้ DNS เข้า Cloudflare) | **GitHub Pages** — `enabridge-lab.github.io/LyricBridge` (Pages source = GitHub Actions) |
| ชื่อ public ของ demo | ใช้ "LyricBridge" หรือชื่ออื่น | **LyricBridge** |
| งบเพดานต่อเดือน | $0 (เครดิตฟรีเท่านั้น — เกินแล้วให้หยุดรับงาน) หรือระบุตัวเลข | **$0 — ใช้เครดิตฟรีเท่านั้น; แตะ $30 ให้หยุดรับงานทันที + ขึ้น banner แจ้งบนหน้าเว็บ** (impl ที่ D5/P3) |
| ภูมิภาคผู้ใช้หลัก | ไทย/เอเชีย → พิจารณา `region="ap-..."` (แพงขึ้น 1.5-1.75x — default คือไม่ล็อก region, ถูกสุด) | **ล็อก `region="ap"` (broad Asia-Pacific)** — เจ้าของยืนยัน. ราคาจริง Modal docs: broad `"ap"` = **1.5x** (~$0.075/เพลง), narrow `"ap-southeast"` = 1.75x. ใช้ broad `"ap"` (ถูกกว่า + availability/cold-start ดีกว่า). เครดิตฟรีรองรับ ~400 เพลง/เดือน |

## A3. Optional (มีแล้วดี ไม่มีก็เริ่มได้)

- **HF token** — โมเดลที่ใช้ (Thai whisper CT2, `airesearch/wav2vec2-large-xlsr-53-th`, demucs)
  เป็น public ทั้งหมด ปกติไม่ต้องใช้ token; ต้องใช้เฉพาะถ้าเปลี่ยน `ASR_MODEL` เป็นโมเดล gated
  → ถ้ามี เก็บเป็น Modal Secret ชื่อ `huggingface` (key `HF_TOKEN`)
- **โดเมนส่วนตัว** สำหรับ frontend (Cloudflare Pages ผูกฟรี)

## A4. ข้อจำกัดของ Starter plan ที่เจ้าของต้องรับทราบ (กระทบ Part C)

- **Custom domain ฝั่ง backend ใช้ไม่ได้** (เป็นของ Team plan $250/เดือน) →
  API URL จะเป็น `https://<workspace>--lyricbridge-web.modal.run` (frontend ใช้โดเมนสวยได้ฟรี ไม่กระทบผู้ใช้)
- **Log เก็บแค่ 1 วัน** → P4 มี mitigation (structured log + ดูผ่าน dashboard เป็นประจำ)
- **Deployment rollback ในตัวไม่มี** → P5 ใช้ git-based rollback (redeploy commit เก่า) แทน
- webhook endpoints จำกัด 8 ตัว → เราใช้ 1 (ASGI app เดียว) ไม่ติด

---

# Part B — Core deploy (D0 → D5): ระบบใช้งานได้จริง

## ทำไม Modal + spec เครื่องที่พอ

- คิดเงินต่อวินาที + scale to zero → ไม่มีคนใช้ = ไม่เสียเงิน
- **GPU ที่พอ = T4** ($0.000164/วินาที ≈ $0.59/ชม.):
  - VRAM 16 GB เหลือเฟือ — pipeline ออกแบบมาให้รันทีละ stage บน 4 GB อยู่แล้ว
    (peak จริง ~4-5 GB: Demucs ~2-3 GB / whisper int8_float16 ~3-4 GB / aligner ~2.9 GB)
  - ~3 นาที/เพลงบน T4 → **~$0.03-0.06/เพลง → เครดิตฟรีรองรับ ~500 เพลง/เดือน**
  - fallback: `gpu=["T4", "L4", "any"]` กัน T4 ไม่ว่าง
- CPU container ของ web endpoint: 0.125 core พอ — ค่าใช้จ่ายจิ๋ว

## ข้อจำกัด Modal ที่บังคับ design (สำคัญมาก)

1. **HTTP request บน web endpoint จำกัด 150 วินาที** — กลไก 303 redirect ต่ออายุ
   **ใช้กับ CORS ไม่ได้** และ frontend เราคนละ origin เสมอ
   → `/karaoke` แบบ block ยาว **ใช้ไม่ได้** → ใช้ pattern ทางการของ Modal:
   web endpoint (CPU) `spawn()` GPU function แล้ว browser poll
   (modal.com/docs/guide/webhook-timeouts § Polling solutions)
2. web container กับ GPU container **คนละเครื่อง ไม่แชร์ดิสก์** — job store
   in-memory + temp file เดิมใช้ข้ามตัวไม่ได้ → ส่งผลผ่าน return value + `modal.Dict`
3. ผลต่อ job ต้องเล็ก: **F1 (encode m4a) — มีใน `server/app/render.py` (`encode_stem`) แล้ว ✅**
   — m4a ~4 MB ใส่ Dict ได้สบาย (เช็ค size limit ของ modal.Dict ใน docs ตอน implement)

## D0 — เตรียมเครื่องมือ

- [ ] เจ้าของ: ส่งมอบ A1 ครบ + ตอบ A2
- [ ] `pip install modal` ใน venv ของ repo, ยืนยัน `modal app list` ไม่ error
- [ ] เพิ่ม Modal rules snippet (modal.com/docs/guide/developing-with-llms) เป็น
      `docs/MODAL_RULES.md` แล้วอ้างจาก `CLAUDE.md` (ส่วน "Where to start")
- [ ] **ทำ F1 ให้เสร็จก่อน** (เหตุผลข้อจำกัด #3)

Acceptance: `modal app list` ผ่าน, F1 merged, `CLAUDE.md` ชี้ไป MODAL_RULES.md

## D1 — Image + โครง App (`deploy/modal_app.py`)

- App ชื่อ `lyricbridge` (kebab-case ตาม convention Modal)
- Image เดียวใช้ร่วมทั้ง web และ GPU function:
  ```python
  image = (
      modal.Image.debian_slim(python_version="3.12")
      .apt_install("ffmpeg", "fonts-tlwg-sarabun")
      .pip_install(...)        # อ่านจาก server/requirements.txt — pin เวอร์ชันเดิม
      .env({"RENDER_FONT": "Sarabun"})
      .add_local_python_source("app")   # โค้ด server/app เดิม — reuse, don't reinvent
  )
  ```
- ห้าม copy โค้ด pipeline — import จาก `server/app` (จัด `sys.path`/package ให้ import
  ได้โดยแตะโค้ดเดิมน้อยสุด); import หนัก (torch ฯลฯ) ไว้ในฟังก์ชัน ไม่ใช่ global scope

Acceptance: `modal run deploy/modal_app.py::smoke` (import ทุก module + `ffmpeg -version`) ผ่าน

## D2 — Model weights ลง Volume

- `modal.Volume` ชื่อ `lyricbridge-models` mount ที่ `/models`
- `download_models()` (CPU function, รันด้วย `modal run` ครั้งเดียว): โหลด whisper CT2
  (ตาม `ASR_MODEL`), wav2vec2 aligner, demucs checkpoint → `volume.commit()`
- env ให้โค้ดเดิมหาเจอ: `HF_HOME=/models/hf` + ตัวแปร model dir —
  **เช็คชื่อ env จริงใน `asr.py` / `separate.py` / `align.py` ห้ามเดา**
- ~7 GB → ~$0.63/เดือน

Acceptance: function list ไฟล์ใน volume เห็นโมเดลครบทุกตัว

## D3 — spawn + poll API (หัวใจของงานนี้)

- **GPU function** `process_song(song_bytes, lang) -> dict`:
  ```python
  @app.function(image=image, gpu=["T4", "L4", "any"],
                volumes={"/models": models_vol},
                timeout=900, max_containers=1)   # ทีละงาน = VRAM invariant เดิม + คุมเงิน
  ```
  ภายใน: bytes → temp → `separate.separate()` → `_run_pipeline()` (reuse!) →
  encode m4a (F1) → return `{"payload": ..., "instrumental_m4a": bytes, "vocal_m4a": bytes}`
  → temp ลบเสมอใน finally
- **Web app** (CPU, `@modal.asgi_app()`) — FastAPI ตัวเล็กใหม่ใน `deploy/modal_app.py`
  (อย่ายัด `server/app/main.py` ทั้งตัว — job store เดิมเป็น in-memory):
  - `POST /jobs/karaoke` → validate ขนาด/ความยาว → `process_song.spawn()` →
    `202 {"job_id": call.object_id}`
  - `GET /jobs/{id}` → `modal.FunctionCall.from_id(id).get(timeout=0)` →
    ยังไม่เสร็จ 202 / เสร็จ: เก็บ stems ลง `modal.Dict` (`lyricbridge-stems`,
    key ฝัง timestamp) แล้วตอบ payload + `instrumental_url`/`vocal_url`
  - `GET /instrumental/{id}` / `GET /vocal/{id}` → bytes จาก Dict → `audio/mp4`
  - `GET /healthz` → `{status, git_sha}` (CI ใช้ตรวจหลัง deploy)
  - sweep: `@app.function(schedule=modal.Period(hours=1))` ลบ Dict entry เก่า
- progress รายละเอียด stage: GPU function เขียน stage ลง `modal.Dict` ผ่าน `on_stage`
  callback ของ `_run_pipeline` (มี hook อยู่แล้ว) — รุ่นแรกใช้ "กำลังประมวลผล" ก่อนได้
- dev ด้วย `modal serve deploy/modal_app.py` (hot-reload) → เสร็จแล้ว `modal deploy`

Acceptance: `curl` submit → poll จน done → GET instrumental เล่นได้ กับเพลงจาก
`server/tests/samples/` 1 เพลง · เวลารวมบน T4 < 5 นาที · อัปโหลดเกิน limit → 413

## D4 — Frontend production

- `web/player.js`: เพิ่ม flow `POST /jobs/karaoke` + poll — **shape เดียวกับ F4 (async queue)
  ของ self-host** (client โค้ดเดียวใช้ได้ทั้ง self-host และ Modal);
  fallback `/karaoke` เดิมถ้า 404 · จำ job_id ใน localStorage (refresh แล้ว resume)
- deploy `web/` ขึ้น **Cloudflare Pages** (หรือ GitHub Pages ตามคำตอบ A2):
  - `apiBase` default = URL Modal production (ยังแก้ได้ใน UI สำหรับ self-hosters)
  - ผูกโดเมนตามคำตอบ A2
- ฝั่ง Modal: `CORS_ORIGINS=<โดเมน frontend>` (ห้ามปล่อย `*` ใน production)

Acceptance: เปิดจากมือถือ/เครื่องนอกบ้าน → อัปโหลด → ร้องคาราโอเกะจบ flow บนโดเมนจริง

## D5 — Guardrails

- `MAX_UPLOAD_MB=30` + ความยาวเพลง ≤ 7 นาที (เช็คก่อน spawn — กันเผาเครดิต)
- คิวค้างเกิน 3 งาน → 429 `{error, stage:"queue"}`
- rate limit ต่อ IP แบบหยาบ (เช่น 5 jobs/ชม./IP — นับใน modal.Dict ก็พอ ไม่ต้องพึ่งของนอก)
- README: ลิงก์ demo + คาดหวัง "งานละ ~3-5 นาที คิวทีละเพลง" + ลิงก์วิธี self-host

Acceptance: 2 งานพร้อมกัน → งานสองต่อคิว · เกิน rate limit → 429 · ข้ามคืนไม่มีคนใช้ ≈ $0

---

# Part C — Production hardening (P1 → P5)

## P1 — CI/CD ด้วย GitHub Actions

- **เจ้าของทำ**: สร้าง Modal API token สำหรับ CI (`modal token new` หรือหน้า settings →
  ได้ `MODAL_TOKEN_ID` + `MODAL_TOKEN_SECRET`) → ใส่เป็น **GitHub Actions secrets**
  (Settings → Secrets and variables → Actions). **ห้ามใส่ token ในไฟล์ใด ๆ ใน repo**
- `.github/workflows/deploy.yml`:
  1. trigger: push ไป `main` (paths: `server/**`, `deploy/**`) + `workflow_dispatch`
  2. job `test`: `pip install -r server/requirements.txt` → `pytest server/` →
     `node --test web/` (ต้องเขียวก่อนถึงจะ deploy)
  3. job `deploy-backend` (needs: test): `pip install modal` →
     `modal deploy deploy/modal_app.py` (อ่าน token จาก env secrets)
  4. job `smoke-prod` (needs: deploy-backend): `curl /healthz` เทียบ `git_sha`
     กับ commit ที่ deploy — ไม่ตรง = fail ดัง ๆ
- frontend: Cloudflare Pages ผูก GitHub repo ตรง ๆ (auto-deploy เมื่อ `web/**` เปลี่ยน,
  ตั้ง build output = `web/`) — ไม่ต้องเขียน workflow เอง / GitHub Pages ใช้
  workflow `actions/deploy-pages` แยก
- **Environments**: ใช้ Modal Environments แยก `dev` / `prod`
  (`modal deploy --env prod`); `modal serve` ระหว่างพัฒนาอยู่ใน `dev` เสมอ —
  กัน dev ทับ production

Acceptance: push commit เล็ก ๆ ไป main → test รัน → backend deploy → smoke ผ่าน
โดยไม่แตะมือเลย · push ที่ test แดง → ไม่ deploy

## P2 — Secrets & config

- ทุกค่า config ผ่าน Modal Secrets / env บน App — **ไม่มี secret ใน git**:
  - `lyricbridge-config`: `CORS_ORIGINS`, `MAX_UPLOAD_MB`, `ASR_MODEL`, `STEM_BITRATE`
  - `huggingface` (optional): `HF_TOKEN`
- เอกสารวิธีหมุน token (CI token หลุด → revoke ใน Modal dashboard → ออกใหม่ → อัปเดต GitHub secret)
- `.gitignore`: ยืนยันว่า `~/.modal.toml` ไม่เกี่ยว, ไม่มี `.env` หลุดใน repo

Acceptance: `git grep` หา token/secret ใน repo = ไม่พบ · เปลี่ยนค่าใน Secret แล้ว
redeploy → มีผลโดยไม่แก้โค้ด

## P3 — Observability & alerting (ภายใต้ข้อจำกัด Starter: log 1 วัน)

- structured log ฝั่งเรา (มี logger อยู่แล้ว): ทุก job log บรรทัดเดียวจบ —
  `job_id, duration_sec, stages timing, gpu, status, error` (machine-parseable)
- `GET /metrics-lite` (ไม่ลับ ไม่ใช่ Prometheus): นับ jobs วันนี้ / สำเร็จ / fail / คิวตอนนี้
  เก็บใน `modal.Dict` — เปิดดูได้จาก browser
- **Billing alert**: เจ้าของตั้งใน Modal dashboard (Settings → Billing) ที่ ~$25
  (ก่อนเครดิตฟรีหมด) — Claude Code ทำแทนไม่ได้ ใส่ไว้ใน checklist เจ้าของ
- ตรวจสุขภาพอัตโนมัติ: scheduled function `modal.Cron("0 1 * * *")` ยิงเพลงทดสอบสั้น 30 วิ
  ผ่าน flow เต็ม → fail = log ERROR เด่น ๆ (และถ้าเจ้าของให้ webhook Discord/LINE Notify
  มาใน Secret ก็ยิงแจ้งเตือน — optional)

Acceptance: ดู dashboard แล้วตอบได้ใน 1 นาทีว่า "วันนี้มีงานกี่งาน fail กี่งาน" ·
canary แดงเมื่อ backend พังจริง

## P4 — ความปลอดภัย & ความเป็นส่วนตัว

- CORS จำกัดโดเมน (D4) · rate limit ต่อ IP (D5)
- ตรวจไฟล์จริงว่าเป็น audio ก่อด้วย soundfile/ffprobe ก่อนเข้า pipeline (มี `_wav_duration`
  pattern อยู่แล้ว) — reject ไฟล์ปลอมเร็ว ๆ ไม่เผา GPU
- privacy page สั้น ๆ ใน frontend: "ไฟล์ถูกประมวลผลแล้วลบภายใน X นาที ไม่เก็บถาวร" —
  ให้ตรงกับ TTL จริงของ Dict sweep
- `docs/COPYRIGHT_AND_LICENSES.md`: เพิ่มหมายเหตุ hosted demo (การแยก stem
  ไม่เปลี่ยนลิขสิทธิ์เพลง — ผู้ใช้รับผิดชอบไฟล์ที่อัปโหลด) — ธรรมเนียม "document risks honestly"

Acceptance: อัปโหลดไฟล์ .exe ปลอมนามสกุล .wav → reject ก่อนถึง GPU ·
privacy/copyright doc ตรงกับพฤติกรรมจริงของระบบ

## P5 — Rollback & disaster runbook (`docs/RUNBOOK.md` — สร้างใหม่)

Starter ไม่มี rollback ในตัว → ใช้ git-based:

- **Rollback backend**: `git checkout <last-good-sha> -- deploy/ server/` →
  `modal deploy` (หรือ rerun workflow ของ commit เก่าผ่าน GitHub UI) — เขียนขั้นตอนละเอียดใน RUNBOOK
- **Rollback frontend**: Cloudflare Pages มี deployment history ในตัว (กด rollback ใน UI ได้ฟรี)
- สถานการณ์ใน RUNBOOK อย่างน้อย: backend deploy พัง / เครดิตหมดกลางเดือน
  (ผลคือ?—function ถูก pause; วิธีปิดรับงานชั่วคราว = ตั้ง `MAX_QUEUED=0` ใน Secret) /
  Modal outage (ชี้ status.modal.com, frontend ขึ้น banner) / โมเดลใน Volume หาย
  (รัน `download_models` ใหม่)
- ทุก deploy ผูก `git_sha` ใน `/healthz` → รู้เสมอว่า prod รันโค้ดไหน

Acceptance: ซ้อม rollback จริง 1 ครั้ง (deploy commit เก่า → healthz ยืนยัน sha → กลับ HEAD)

---

# สรุปค่าใช้จ่าย production (Starter plan)

| รายการ | ราคา |
|---|---|
| GPU T4 ต่อเพลง (~3 นาที) | ~$0.03–0.06 |
| Web endpoint CPU (idle = $0, scale to zero) | จิ๋วมาก |
| Volume โมเดล ~7 GB | ~$0.63/เดือน |
| Canary 1 เพลงสั้น/วัน | ~$0.30/เดือน |
| Cloudflare Pages / GitHub Pages + Actions | ฟรี |
| เครดิตฟรี Starter | $30/เดือน |
| **สุทธิ** | **$0 จนกว่าจะเกิน ~450-500 เพลง/เดือน** — เกินแล้วค่อยพิจารณา Team plan |

# ลำดับโดยรวม

```
F1 (m4a encode, done) → D0 → D1 → D2 → D3 → D4 → D5   ← ระบบใช้งานได้
                                            ↓
                              P1 (CI/CD) → P2 (secrets) → P3 (observability)
                              → P4 (security/privacy) → P5 (runbook)          ← production-grade
```

ถ้าทำ F4 (async queue ฝั่ง self-host) ทีหลัง ให้ใช้ API shape เดียวกับ D3 —
client โค้ดเดียวใช้ได้ทั้งสองที่.

# Checklist สุดท้ายของเจ้าของ (มนุษย์เท่านั้น)

- [ ] A1: Modal login (`modal setup`), repo บน GitHub, Cloudflare/GitHub Pages พร้อม
- [ ] A2: ตอบ 4 คำถาม (โดเมน, ชื่อ, งบ, region) ลงตารางใน Part A
- [ ] P1: สร้าง CI token + ใส่ GitHub Actions secrets
- [ ] P3: ตั้ง billing alert ใน Modal dashboard (~$25)
- [ ] ทดลองใช้จริง 1 เพลงหลัง D4 และอีกครั้งหลัง P5
