# M0 cloud ASR service -- CPU image (the self-host promise: runs without a GPU).
# A GPU variant can swap the base for an nvidia/cuda runtime later.
FROM python:3.12-slim

# ffmpeg: audio decode (faster-whisper/whisperx), .mp4 demux (M1 /separate),
#         and the karaoke burn-in (M3 /render).
# (Kept as its own early layer so it stays build-cache-friendly; the Thai font is
#  added later — see below — so a font change never busts the big pip layer.)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install CPU torch/torchaudio first so whisperx/audio-separator don't pull CUDA wheels.
RUN pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
# whisperx/audio-separator depend on torch but requirements.txt doesn't pin it, so pip
# would re-resolve torch from default PyPI -> the 887 MB CUDA wheel, clobbering
# the CPU torch above and bloating this "CPU image" (and timing out on the big
# download). Adding the CPU index here makes pip pick torch 2.8.0+cpu (~190 MB):
# its local-version tag (+cpu) sorts ABOVE plain 2.8.0, so the resolver prefers
# it. timeout+retries keep the remaining big ML wheels resilient on a slow link.
# build-essential: some deps (diffq, via demucs/audio-separator) ship only as
# source and compile a C/Cython extension — the slim base has no gcc. Install
# the toolchain, build, then purge it in the SAME layer so the image stays lean
# (the compiled .so is kept; the compiler is not needed at runtime).
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && pip install --no-cache-dir --timeout=300 --retries=10 \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        -r requirements.txt \
    && apt-get purge -y build-essential \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# fonts-thai-tlwg (incl. Sarabun): libass burns real Thai glyphs instead of tofu
# (PRD Thai rule). Late layer so this never invalidates the big pip layer above.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-thai-tlwg \
    && rm -rf /var/lib/apt/lists/*

COPY app ./app

ENV ASR_MODEL=large-v3 \
    ASR_DEVICE=cpu \
    ALIGN_MODEL=airesearch/wav2vec2-large-xlsr-53-th \
    SEPARATION_MODEL=htdemucs_ft.yaml \
    SEPARATION_DEVICE=cpu \
    SEPARATION_MODEL_DIR=/app/models/audio-separator \
    DEMUCS_SEGMENT=7 \
    RENDER_FONT=Sarabun \
    LOG_LEVEL=INFO

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
