"""LyricBridge — cloud ASR service and separation prototype.

Stateless FastAPI service: POST a vocal .wav -> receive word-level
Thai-timed LRC + ASS + JSON. M1 also exposes server-side separation so the
full song -> stems -> ASR pipeline can be tested before on-device separation.
"""

__version__ = "0.2.0"
