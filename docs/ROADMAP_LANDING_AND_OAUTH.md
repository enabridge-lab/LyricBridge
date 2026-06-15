# ROADMAP — Landing page + Google OAuth (next phases)

> Future work, owner-approved direction. **Keep Modal for the GPU pipeline** (don't move to
> Railway). Phases ship independently. Style reference: https://talebridge.enabridge.ai/
> (product landing, warm tone, bilingual TH).
>
> **→ Detailed build spec (files, functions, gotchas, acceptance) for each phase below:
> `docs/ROADMAP_IMPL.md`.** This doc = the *what/why*; ROADMAP_IMPL = the *how*.

---

## Phase L — Landing / About page

**Goal:** anyone who opens the site immediately understands what LyricBridge is and can try it
in one click. Public, no login required to view.

### Structure (mapped from talebridge → LyricBridge)
1. **Hero** — name + one-line tagline + primary CTA.
   - TH: "เปลี่ยนเพลงโปรดให้เป็นคาราโอเกะ ซับไทยซิงก์ทีละคำ"
   - EN: "Turn any song into word-synced Thai karaoke."
   - CTA: "ลองเลย — ฟรี" → links/scrolls to the player.
2. **ภาพรวมใน 1 นาที** — one paragraph: full song → remove vocals → AI Thai lyrics, word-timed →
   sing in the browser or export a karaoke video.
3. **How it works (4 step cards)** — แบบ talebridge:
   - อัปเพลง → ตัดเสียงร้อง (AI/Demucs) → ถอด+จับเวลาเนื้อไทย (Whisper-Thai + wav2vec2) → ร้อง/เซฟวิดีโอ
4. **Feature highlights (emoji cards)**:
   - 🎯 ไฮไลต์ทีละคำเรียลไทม์ · 🇹🇭 โฟกัสเพลงไทย/ลูกทุ่ง · ✏️ แก้เนื้อเองแล้ว re-render ได้
   - 🎤 vocal guide (เปิดเสียงร้องต้นฉบับช่วยจับคีย์) · 🎬 export วิดีโอคาราโอเกะ
5. **Trust / privacy** (จุดขายจริง — ตรงกับ stateless promise):
   - "ไฟล์ที่อัปถูกประมวลผลแล้วลบภายในไม่กี่นาที — ไม่เก็บถาวร" (ให้ตรงกับ TTL จริง)
6. **Open-source / self-host** — "MIT · รันเองบนเครื่องคุณได้" + ลิงก์ GitHub repo.
7. **CTA ปิดท้าย** + footer (© year, ลิงก์ privacy/copyright).

### Design / tech
- **Static, no build** — plain HTML/CSS so it stays deployable to GitHub Pages as-is.
  (If it grows into auth/history/dashboards later → migrate to a small Vite SPA, still static.)
- **Bilingual TH/EN** toggle (เหมือน README.md/README.th.md), TH เป็น default.
- **Mobile-first + accessible (WCAG AA)**: semantic HTML, alt text, keyboard nav, ≥4.5:1 contrast.
  ใช้ได้เต็มที่โดยไม่ต้อง login.
- **ห้ามโชว์เพลงลิขสิทธิ์เป็นตัวอย่าง** — ใช้ screenshot/gif ของ player หรือคลิปสั้นที่ปลอดลิขสิทธิ์เท่านั้น.

### ⚠️ Routing note (สำคัญตอน implement)
ตอนนี้ `web/index.html` = ตัว player และ CI inject `lyricbridge-api-base` meta ลง `index.html`.
ถ้า landing กลายเป็น `index.html` ใหม่ → ย้าย player ไป `web/app.html` (หรือ `/app/`) **และ
อัปเดต sed ใน `.github/workflows/deploy.yml` ให้ inject meta ลงไฟล์ player ตัวใหม่** (ไม่งั้น
หน้า player จะหา backend ไม่เจอ). asset ต้องเป็น relative path (subpath `/LyricBridge/`).

### Acceptance
- เปิดจากมือถือ → เข้าใจว่าโปรเจกต์คืออะไรใน <10 วิ → กด CTA ไปหน้า player แล้วใช้งานได้จริง
- ผ่าน Lighthouse a11y ≥ 90, ใช้งานได้ทั้ง TH/EN, ไม่ต้อง login

---

## Phase A — Google OAuth (stateless, no DB, no Railway)

**Goal:** "login เพื่อกันใช้มั่ว + คุมโควตา" — **ไม่เก็บข้อมูลผู้ใช้/เนื้อ/ไฟล์** (คงสัญญา stateless).

### Design
```
Frontend (Google Identity Services) → ผู้ใช้ login → ได้ ID token (JWT)
   │  แนบ Authorization: Bearer <JWT> ทุก POST /jobs/karaoke
   ▼
Modal web → verify JWT (ลายเซ็น + aud + exp กับ public keys ของ Google, cache keys)
          → quota ต่อ user ใน modal.Dict key = google_sub + เดือน (เช่น free 10 เพลง/เดือน)
          → spawn GPU เหมือนเดิม
```
- **ไม่มี DB, ไม่มี session store, ไม่มี Railway.** verify JWT แบบ stateless ในฝั่ง Modal web.
- Quota: ต่อยอด rate-limit เดิม (เปลี่ยน key จาก IP → `google_sub`). คง $30 kill switch ไว้.
- Landing + การดูเว็บ **ไม่ต้อง login**; login เฉพาะตอนจะสร้างคาราโอเกะ (gate ที่ `/jobs/karaoke`).
- Secret ใหม่ใน `lyricbridge-config`: `GOOGLE_CLIENT_ID` (ใช้ตรวจ `aud` ของ JWT).

### สิ่งที่ยอมแลก (รับทราบแล้ว)
- ไม่มีประวัติเพลง / ไม่มี save เนื้อที่แก้ (ผู้ใช้โหลดผลทันที). quota ใน Dict ไม่ durable 100%
  ตอน redeploy — รับได้สำหรับ demo. ถ้าวันหน้าต้องการ history/quota ถาวร → ค่อยเติม serverless
  Postgres (Supabase/Neon) เก็บแค่ `users` + `usage` (ยังห้ามเก็บไฟล์เสียง/stem/วิดีโอ).

### Acceptance
- ยังไม่ login → ดู landing + player ได้ แต่กดสร้าง → ขอ login
- login Google สำเร็จ → สร้างได้; เกินโควตาเดือน → 429 พร้อมข้อความชัด
- ไม่มี user data/audio ถูก persist; privacy doc ยังตรงกับพฤติกรรมจริง

---

---

## Phase D — Demo & wait experience (quick wins, conversion)

**Goal:** คนเข้าใจ+อยากลองทันที และช่วงรอ ~3 นาทีไม่น่าเบื่อ.

- **D1 — เดโมสำเร็จรูปบน landing**: ฝังตัวอย่างคาราโอเกะ 1 เพลง (**เพลงปลอดลิขสิทธิ์/CC หรือ
  เสียงร้องของเจ้าของเอง เท่านั้น**) — payload (`words`+`lrc`+`ass`) + instrumental m4a สั้นๆ
  เก็บเป็น static asset ใน `web/` แล้วกด "ดูตัวอย่าง" → player เล่นไฮไลต์ทีละคำ **โดยไม่ยิง
  backend / ไม่ต้องอัปไฟล์ / ไม่ใช้ GPU**. นี่คือ asset ที่ pre-generate ครั้งเดียวแล้ว commit.
- **D2 — เล่น instrumental ทันทีที่แยกเสียงเสร็จ**: ส่ง stage "separating done" + instrumental
  url ให้ก่อน แล้วค่อย stream เนื้อตามมา (ไม่ต้องรอ align/build จบ). *แตะ backend เล็กน้อย*
  (ลำดับการ return ใน `process_song` / progress).
- **D3 — โชว์เนื้อค่อยๆ โผล่ + ETA ระหว่างรอ**: ใช้ `progress` Dict ที่มีอยู่ ส่ง partial
  segments/stage ให้ player แสดง + แสดงเวลาโดยประมาณจาก duration.

**Acceptance:** เปิด landing → กดดูตัวอย่าง → คาราโอเกะเล่นทันทีไม่มีการอัป/รอ · งานจริงเห็น
instrumental + เนื้อทยอยมาก่อน pipeline จบ.

---

## Phase S — Stage / sing (ทำตัว core ให้ "ฟิน")  *(ส่วนใหญ่ client-side, ไม่เพิ่มต้นทุน server)*

- **S1 — โหมดเต็มจอ "ขึ้นเวที"** + นับถอยหลัง 3-2-1 ก่อนเข้าท่อน + ลูกบอลเด้ง/เส้นกวาดตามคำ.
- **S2 — ปรับความเร็วเพลง (ช้าลงเพื่อซ้อม) + loop ท่อนที่เลือก** — playbackRate + คุม A/B loop
  ฝั่ง client ล้วน.
- **S3 — ธีมสี/ขนาดฟอนต์/พื้นหลัง** เลือกได้ใน player (ต่อยอด F8; จำค่าใน localStorage).
- **S4 — อัดเสียงตัวเองร้อง (signature feature)**: `MediaRecorder` ในเบราว์เซอร์ → มิกซ์กับ
  instrumental (Web Audio API) → ให้โหลดเก็บ. **ทำฝั่ง client ล้วน = ยัง stateless, ไม่อัป
  เสียงผู้ใช้ขึ้น server เลย** (ขอ permission ไมค์, จัดการ autoplay/echo).

**Acceptance:** กดเต็มจอแล้วร้องตามได้ลื่น · ช้าลง/loop ได้ · อัดเสียงตัวเอง → ได้ไฟล์มิกซ์
โหลดเก็บ โดยไม่มีเสียงผู้ใช้ออกจากเครื่องไป server.

---

## Phase E — Edit & learner reach (เปลี่ยนจุดอ่อนเป็นจุดขาย)

- **E1 — แก้เนื้อ inline ลื่นๆ**: คลิกคำที่ confidence ต่ำ (ขีดเส้นใต้ส้ม จาก F3) → พิมพ์แก้ตรงนั้น
  → re-render ผ่าน `/render/<job_id>` ที่มีอยู่. ใช้ F3/F6 ที่ทำไว้แล้ว แค่ขัด UX ให้เนียน.
- **E2 — โรมัน + คำแปลอังกฤษ toggle**: โรมันมี F7 แล้ว; คำแปลไทย→อังกฤษเป็นของใหม่ —
  *แตะ backend/ต้นทุน* (เรียกโมเดลแปล) → ทำเป็น optional, cache, หรือเฟสหลัง. ระวังงบ.

**Acceptance:** แก้คำผิดแล้ววิดีโอ/ไฮไลต์อัปเดตตาม · เปิด/ปิดโรมัน(+แปล)ได้ ไม่ติดออกไปกับไฟล์ export.

---

## Phase O — Output & share

- **O1 — export วิดีโอใส่ภาพพื้นหลัง/การ์ดชื่อเพลง**: เพิ่ม field ให้ `/render/<job_id>`
  (background image/title) — *แตะ render ฝั่ง backend*.
- **O2 — ลิงก์แชร์ชั่วคราว**: เก็บผลใน `modal.Dict` แบบมี TTL + private slug, **หมดอายุเร็ว**.
  ⚠️ ลิขสิทธิ์: วิดีโอมี instrumental ลิขสิทธิ์ → แชร์ต้องชั่วคราว/ส่วนตัว ไม่ทำ public library.

**Acceptance:** export มีพื้นหลัง/การ์ดได้ · ลิงก์แชร์เปิดได้ชั่วคราวแล้วหมดอายุจริง.

---

## ลำดับแนะนำ
1. **Phase L (landing)** + **D1 (เดโม)** — เข้าถึง/เข้าใจ/conversion ทันที, ไม่แตะ backend, ฟรี.
2. **Phase S (stage/sing)** — client-side เกือบหมด, เพิ่ม "ความฟิน" โดยไม่เพิ่มต้นทุน → คุ้มสุด.
3. **Phase E (edit)** E1 ก่อน (ใช้ของที่มี), E2 (แปล) ทีหลังเพราะมีต้นทุน.
4. **Phase D (D2/D3)** + **Phase O** — ปรับ backend เล็กน้อย, ทำเมื่อ core/landing นิ่งแล้ว.
5. **Phase A (OAuth)** — เมื่อเริ่มมีผู้ใช้เยอะ อยากคุมโควตา/ต้นทุน.

**ทุกเฟส: ไม่ต้องใช้ Railway, ไม่ต้องมี DB, คงสัญญา stateless/ไม่เก็บไฟล์-เนื้อ-เสียงผู้ใช้.**
ฟีเจอร์ที่แตะ GPU/โมเดลเพิ่ม (E2 แปล) ให้ดูงบ Modal ($30) ก่อนเปิด.
