"""Pydantic request/response models — the M0 API contract (PRD section 7.3)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

OutputFormat = Literal["lrc", "ass", "json"]
Stage = Literal["separate", "asr", "align", "tokenize", "build"]


class Word(BaseModel):
    """A single tokenized Thai word with its time span (seconds)."""

    text: str
    start: float = Field(..., description="Word start time in seconds")
    end: float = Field(..., description="Word end time in seconds")
    # F3: ASR confidence 0..1, segment-level (every word in a segment shares the
    # segment's score — whisper's tokens don't map 1:1 onto PyThaiNLP tokens).
    # Optional so payloads/JSON files from before F3 still load everywhere.
    confidence: float | None = None
    # F6: True when this word's timing was GUESSED (interpolated) rather than
    # taken from real char alignment. Default False keeps old payloads valid.
    interpolated: bool = False
    # F7: romanized reading (PyThaiNLP royin) for Thai learners. Optional.
    roman: str | None = None


class TranscribeResponse(BaseModel):
    """Successful /transcribe response."""

    language: str
    duration_sec: float
    words: list[Word]
    lrc: str
    ass: str
    # True when word timings came from real forced alignment; False when we
    # degraded to segment-level interpolation (Thai align model missing/weak).
    aligned: bool = True
    # How many ASR segments fell back to interpolation (0 = fully aligned). A
    # high count vs len(words' segments) means sync is mostly guessed timing.
    degraded_segment_count: int = 0
    total_segment_count: int = 0
    # Per-stage wall time in seconds (separate/asr/align/build), for perf tuning.
    # Omitted unless EXPOSE_TIMINGS is on. None keeps the field out of the JSON.
    timings_sec: dict[str, float] | None = None


class ErrorResponse(BaseModel):
    """4xx/5xx response — names the failing pipeline stage."""

    error: str
    stage: Stage


class HealthResponse(BaseModel):
    status: str
    device: str
    asr_model: str
    separation_model: str | None = None


class VersionResponse(BaseModel):
    app_version: str
    asr_model: str
    # Pinned HF commit of the ASR model (None = latest/unpinned or a local path).
    # Surfaced so a deployment can confirm it is running reproducible weights.
    asr_model_revision: str | None = None
    separation_model: str
    align_available: bool
    align_load_error: str | None = None
    git_sha: str
