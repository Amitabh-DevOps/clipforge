"""Step 2 — transcribe with faster-whisper, word-level timestamps."""
from __future__ import annotations

import threading

import config

_model = None
_model_lock = threading.Lock()


def _get_model():
    global _model
    with _model_lock:
        if _model is not None:
            return _model
        from faster_whisper import WhisperModel

        device = config.WHISPER_DEVICE
        compute = config.WHISPER_COMPUTE
        if device == "auto":
            try:
                import ctranslate2

                device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
            except Exception:
                device = "cpu"
        if compute == "auto":
            compute = "float16" if device == "cuda" else "int8"
        _model = WhisperModel(config.WHISPER_MODEL, device=device, compute_type=compute)
        return _model


def transcribe(video_path: str, progress_cb=None) -> dict:
    """Returns {"language": str, "segments": [...], "words": [...]}.

    segments: [{start, end, text}]
    words:    [{start, end, word}]
    """
    model = _get_model()
    task = config.WHISPER_TASK if config.WHISPER_TASK in ("transcribe", "translate") else "transcribe"
    seg_iter, info = model.transcribe(
        video_path,
        task=task,
        word_timestamps=True,
        vad_filter=True,
    )

    segments, words = [], []
    total = info.duration or 1.0
    for seg in seg_iter:
        segments.append({"start": seg.start, "end": seg.end, "text": seg.text.strip()})
        for w in seg.words or []:
            words.append({"start": w.start, "end": w.end, "word": w.word.strip()})
        if progress_cb:
            progress_cb(min(seg.end / total, 1.0))

    return {"language": info.language, "segments": segments, "words": words}
