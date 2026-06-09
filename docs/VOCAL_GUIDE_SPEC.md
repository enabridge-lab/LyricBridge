# Vocal Guide Feature — Implementation Spec

> **เป้าหมาย:** เมื่อเล่นคาราโอเกะ ให้เสียงร้องต้นฉบับ (vocal stem) เล่นควบคู่กับ instrumental
> โดยมี slider ปรับ volume ได้ (0–100%) เพื่อให้ผู้ใช้ใช้เป็น "ไกด์นำร้อง"

---

## Design Decisions

- **2 `<audio>` elements** (ไม่ใช่ Web Audio API) — ง่ายกว่า, ไม่ต้อง buffer ทั้งเพลงเข้า RAM
- **Sync**: event listener บน main audio (`play`, `pause`, `seeked`) → mirror ไป vocal audio
- **Drift** ~50ms ในทางปฏิบัติ — รับได้สำหรับ vocal guide
- **Degrade silently**: ถ้า vocal fetch fail → ไม่ crash, ไม่แสดง panel
- **vocal_url optional**: `/karaoke` ยัง backward-compatible ถ้าไม่มี vocal

---

## Backend Changes — `server/app/main.py`

### B1 · เพิ่ม vocal job store

หลัง `_JOBS_DIR` declaration เพิ่ม:

```python
# Vocal stems: TTL-only cleanup (no pop-on-take, เพื่อให้ re-fetch ได้)
_VOCAL_DIR = Path(tempfile.mkdtemp(prefix="karaoke_vocals_"))
_vocal_jobs_lock = threading.Lock()
_vocal_jobs: dict[str, tuple[Path, float]] = {}  # job_id -> (vocals_path, expiry)
```

### B2 · เพิ่ม `_store_vocal` และ `_get_vocal`

```python
def _store_vocal(job_id: str, src: Path) -> None:
    """Move a vocal stem into the vocal store under the given job_id."""
    dest_dir = _VOCAL_DIR / job_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "vocals.wav"
    shutil.move(str(src), str(dest))
    with _vocal_jobs_lock:
        _vocal_jobs[job_id] = (dest, time.time() + INSTRUMENTAL_TTL_SEC)


def _get_vocal(job_id: str) -> Path | None:
    """Return the vocal path for job_id if it exists and hasn't expired, else None."""
    with _vocal_jobs_lock:
        entry = _vocal_jobs.get(job_id)
    if entry is None:
        return None
    path, expiry = entry
    if time.time() > expiry or not path.exists():
        shutil.rmtree(path.parent, ignore_errors=True)
        with _vocal_jobs_lock:
            _vocal_jobs.pop(job_id, None)
        return None
    return path
```

### B3 · แก้ `/karaoke` endpoint

ใน `/karaoke` หลัง block ที่เรียก `_store_instrumental` (บรรทัดประมาณ 494) เพิ่ม:

```python
job_id = _store_instrumental(result.instrumental_path)

# Store vocal stem for the guide feature.
# result.vocals_path ยังอยู่ใน tmpdir ณ จุดนี้ — move ก่อน finally cleanup
try:
    _store_vocal(job_id, result.vocals_path)
except Exception:
    logger.warning("could not store vocal stem for job %s", job_id)
    # degrade: ไม่ใส่ vocal_url ใน response

_set_progress(pid, "done")

payload = resp.model_dump()
payload["job_id"] = job_id
payload["instrumental_url"] = f"/instrumental/{job_id}"
# ใส่ vocal_url เฉพาะถ้า store สำเร็จ
if _get_vocal(job_id) is not None:
    payload["vocal_url"] = f"/vocal/{job_id}"
return JSONResponse(content=payload)
```

> ⚠️ **หมายเหตุ**: `result.vocals_path` ถูกลบใน `finally: shutil.rmtree(tmpdir)` เดิม
> เพราะ `_store_vocal` ทำ `shutil.move` ออกไปก่อน finally จึงไม่กระทบ
> แต่ต้องเรียก `_store_vocal` **ก่อน** `finally` cleanup — ซึ่ง block นี้อยู่ใน `try` body
> ก่อน `finally` อยู่แล้ว ✓

### B4 · เพิ่ม `GET /vocal/{job_id}` endpoint

```python
@app.get("/vocal/{job_id}")
def get_vocal(job_id: str):
    """Stream a /karaoke job's vocal stem (TTL-bounded, re-fetchable)."""
    path = _get_vocal(job_id)
    if path is None:
        return _err(404, "vocal not found or expired", "vocal")
    return FileResponse(
        path,
        media_type="audio/wav",
        filename="vocals.wav",
        # ไม่มี BackgroundTask ลบ — ปล่อยให้ sweeper ลบตาม TTL
    )
```

### B5 · แก้ `_sweep_jobs` ให้ sweep vocal ด้วย

ใน `_sweep_jobs()` หลัง existing sweep block เพิ่ม:

```python
# Sweep expired vocal stems
with _vocal_jobs_lock:
    expired_vocals = [
        (jid, path)
        for jid, (path, expiry) in list(_vocal_jobs.items())
        if now > expiry
    ]
    for jid, _ in expired_vocals:
        _vocal_jobs.pop(jid, None)
for _, path in expired_vocals:
    shutil.rmtree(path.parent, ignore_errors=True)
```

---

## Frontend Changes

### F1 · `web/index.html` — เพิ่ม Vocal Guide panel

เพิ่มหลัง sync-offset controls (มองหา `id="syncOffsetVal"`) — วางไว้ในกลุ่ม playback tools เดียวกัน:

```html
<!-- Vocal Guide -->
<div class="vocal-guide-panel" id="vocalGuidePanel" hidden>
  <label class="vg-toggle">
    <input type="checkbox" id="vocalGuideToggle" />
    <span>🎤 เสียงนำร้อง <span class="en">Vocal Guide</span></span>
  </label>
  <div class="vg-slider-row" id="vocalSliderRow">
    <label for="vocalVolume" class="vg-label">ดัง</label>
    <input type="range" id="vocalVolume" min="0" max="100" value="30" />
    <span id="vocalVolumeVal">30%</span>
  </div>
</div>
```

CSS (เพิ่มใน `style.css`):

```css
.vocal-guide-panel {
  display: flex;
  align-items: center;
  gap: 1rem;
  flex-wrap: wrap;
  padding: 0.5rem 0.75rem;
  background: var(--surface-alt, #f3f4f6);
  border-radius: 8px;
  margin-top: 0.5rem;
}
.vg-toggle { display: flex; align-items: center; gap: 0.4rem; cursor: pointer; font-size: 0.9rem; }
.vg-slider-row { display: flex; align-items: center; gap: 0.5rem; }
.vg-label { font-size: 0.85rem; color: var(--text-muted, #666); }
#vocalVolume { width: 120px; }
#vocalVolumeVal { font-size: 0.85rem; min-width: 3ch; }
```

### F2 · `web/player.js` — เพิ่ม state ใน `init()`

ใน `els` object เพิ่ม:

```js
vocalGuidePanel: $("vocalGuidePanel"),
vocalGuideToggle: $("vocalGuideToggle"),
vocalVolume: $("vocalVolume"),
vocalVolumeVal: $("vocalVolumeVal"),
vocalSliderRow: $("vocalSliderRow"),
```

ใต้ `let editMode = false;` เพิ่ม:

```js
let vocalAudio = null;
let vocalGuideVol = Number(localStorage.getItem("vocalGuideVol") ?? "") || 0.3;
```

### F3 · เพิ่ม `loadVocalGuide(blob)` function

```js
function loadVocalGuide(blob) {
  // Clean up previous vocal audio if re-uploading
  if (vocalAudio) {
    vocalAudio.pause();
    URL.revokeObjectURL(vocalAudio.src);
    vocalAudio = null;
  }

  vocalAudio = new Audio();
  vocalAudio.src = URL.createObjectURL(blob);
  vocalAudio.volume = vocalGuideVol;

  // Sync with main audio
  _wireVocalSync();

  // Show panel + restore slider UI
  if (els.vocalGuidePanel) {
    els.vocalGuidePanel.hidden = false;
    if (els.vocalVolume) {
      els.vocalVolume.value = String(Math.round(vocalGuideVol * 100));
      if (els.vocalVolumeVal) els.vocalVolumeVal.textContent = Math.round(vocalGuideVol * 100) + "%";
    }
    // default: guide OFF (muted) until user enables checkbox
    if (els.vocalGuideToggle) els.vocalGuideToggle.checked = false;
    vocalAudio.volume = 0;
    if (els.vocalSliderRow) els.vocalSliderRow.style.opacity = "0.4";
  }
}
```

### F4 · เพิ่ม `_wireVocalSync()` — KEY STEP ★

```js
function _wireVocalSync() {
  if (!vocalAudio) return;

  // Re-sync currentTime on seeked (drag / click on timeline)
  els.audio.addEventListener("seeked", () => {
    if (!vocalAudio) return;
    vocalAudio.currentTime = els.audio.currentTime;
  });

  // Mirror play/pause
  els.audio.addEventListener("play", () => {
    if (!vocalAudio) return;
    // Re-align time in case of drift before resuming
    vocalAudio.currentTime = els.audio.currentTime;
    vocalAudio.play().catch(() => {/* autoplay policy — ignored */});
  });

  els.audio.addEventListener("pause", () => {
    vocalAudio?.pause();
  });
}
```

> **Note**: เรียก `_wireVocalSync()` ได้หลาย track (แต่ listener ซ้ำจะ attach ซ้ำ)
> ให้ใช้ `{ once: false }` และ guard ด้วย `if (!vocalAudio)` ใน handler ป้องกัน stale ref

### F5 · Vocal Guide toggle + slider wiring

เพิ่มใน `init()` หลัง sync offset wiring:

```js
// --- vocal guide panel ---
if (els.vocalGuideToggle) {
  els.vocalGuideToggle.addEventListener("change", (e) => {
    if (!vocalAudio) return;
    const on = e.target.checked;
    if (on) {
      // Restore volume from slider
      vocalAudio.volume = vocalGuideVol;
      if (els.vocalSliderRow) els.vocalSliderRow.style.opacity = "1";
    } else {
      vocalAudio.volume = 0;
      if (els.vocalSliderRow) els.vocalSliderRow.style.opacity = "0.4";
    }
  });
}

if (els.vocalVolume) {
  els.vocalVolume.value = String(Math.round(vocalGuideVol * 100));
  els.vocalVolume.addEventListener("input", (e) => {
    vocalGuideVol = Number(e.target.value) / 100;
    if (els.vocalVolumeVal) els.vocalVolumeVal.textContent = e.target.value + "%";
    localStorage.setItem("vocalGuideVol", String(vocalGuideVol));
    if (vocalAudio && els.vocalGuideToggle?.checked) {
      vocalAudio.volume = vocalGuideVol;
    }
  });
}
```

### F6 · แก้ `runKaraoke()` — fetch vocal URL

ใน `runKaraoke()` หลัง `loadAudio(...)` และ `loadModel(payload)` เพิ่ม:

```js
// Vocal guide: fetch the vocal stem if the server provided one
if (payload.vocal_url) {
  try {
    const vocalRes = await fetch(base.replace(/\/$/, "") + payload.vocal_url);
    if (vocalRes.ok) {
      const vocalBlob = await vocalRes.blob();
      loadVocalGuide(new File([vocalBlob], "vocals.wav", { type: vocalBlob.type || "audio/wav" }));
    }
  } catch {
    // Vocal fetch failed — degrade silently (guide panel stays hidden)
  }
}
```

---

## Tests to Add — `server/tests/test_api.py`

ค้นหา existing `/karaoke` test แล้วเพิ่ม assertions:

```python
def test_karaoke_response_has_vocal_url(client, sample_song):
    """POST /karaoke should return vocal_url pointing to /vocal/<job_id>."""
    resp = client.post("/karaoke", files={"file": sample_song}, data={"lang": "th"})
    assert resp.status_code == 200
    payload = resp.json()
    assert "vocal_url" in payload
    assert payload["vocal_url"].startswith("/vocal/")

def test_get_vocal_returns_audio(client, sample_song):
    """GET /vocal/<job_id> should return 200 audio/wav."""
    resp = client.post("/karaoke", files={"file": sample_song}, data={"lang": "th"})
    job_id = resp.json()["job_id"]
    vocal_resp = client.get(f"/vocal/{job_id}")
    assert vocal_resp.status_code == 200
    assert "audio" in vocal_resp.headers["content-type"]

def test_get_vocal_invalid_returns_404(client):
    """GET /vocal/<invalid> should return 404."""
    resp = client.get("/vocal/nonexistent_job_id")
    assert resp.status_code == 404
```

---

## Acceptance Criteria

- [ ] `POST /karaoke` response มี `vocal_url` field (เช่น `/vocal/<job_id>`)
- [ ] `GET /vocal/<job_id>` returns `audio/wav`, HTTP 200
- [ ] `GET /vocal/invalid` returns 404
- [ ] Web player: เมื่อ upload เพลงผ่าน Step 0 → vocal guide panel ปรากฏ
- [ ] Checkbox OFF (default) → เสียงร้อง silent; เปิด checkbox → เสียงดัง = ค่า slider
- [ ] Slider ปรับ volume real-time, persist ผ่าน localStorage
- [ ] Seek / pause / play → vocal track ตาม main audio ทันที
- [ ] ถ้า server ไม่ส่ง `vocal_url` (backward compat) → panel ไม่แสดง, ไม่ error
- [ ] 50 existing tests ยังผ่านทั้งหมด

---

## Files to Touch Summary

| File | การเปลี่ยน |
|------|-----------|
| `server/app/main.py` | เพิ่ม `_vocal_jobs`, `_store_vocal`, `_get_vocal`, `GET /vocal/{job_id}`, แก้ `/karaoke` + `_sweep_jobs` |
| `web/player.js` | เพิ่ม `vocalAudio` state, `loadVocalGuide`, `_wireVocalSync`, slider wiring, แก้ `runKaraoke` |
| `web/index.html` | เพิ่ม `#vocalGuidePanel` div |
| `web/style.css` | เพิ่ม `.vocal-guide-panel` styles |
| `server/tests/test_api.py` | เพิ่ม 3 tests |
