from __future__ import annotations

import re


# One second avoids treating ordinary between-word gaps as interview pauses.
MIN_MEANINGFUL_PAUSE_SECONDS = 1.0
MIN_WORDS_FOR_RELIABLE_PACE = 20

FILLER_PATTERNS = {
    "um": r"\bum+\b",
    "uh": r"\buh+\b",
    "erm": r"\berm+\b",
    "mm": r"\bm{2,}\b",
    "hmm": r"\bhm+\b",
    "you know": r"\byou\s+know\b",
}


def _non_negative_float(value):
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return None


def _non_negative_int(value):
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return None


def _extract_words(transcript):
    return re.findall(
        r"\b[\w'-]+\b",
        str(transcript or "").strip(),
        flags=re.UNICODE,
    )


def _count_fillers(transcript):
    text = str(transcript or "").casefold()
    fillers = {}
    for label, pattern in FILLER_PATTERNS.items():
        count = len(re.findall(pattern, text, flags=re.IGNORECASE))
        if count:
            fillers[label] = count
    return fillers


def _calculate_words_per_minute(word_count, speech_duration_seconds):
    if word_count < MIN_WORDS_FOR_RELIABLE_PACE:
        return None, "insufficient_speech"
    if speech_duration_seconds is None or speech_duration_seconds <= 0:
        return None, "unavailable"
    return round((word_count / speech_duration_seconds) * 60, 1), "available"


def analyse_delivery(
    transcript,
    *,
    duration_seconds=None,
    speech_duration_seconds=None,
    pause_count=None,
    longest_pause_seconds=None,
    vad_available=True,
):
    """Build delivery measurements from immutable raw ASR/VAD evidence."""

    total_duration = _non_negative_float(duration_seconds)
    speech_duration = _non_negative_float(speech_duration_seconds)
    if speech_duration is None and total_duration is not None and vad_available:
        # Compatibility for established callers that only supplied duration.
        speech_duration = total_duration

    observed_pause_count = _non_negative_int(pause_count) if vad_available else None
    longest_pause = _non_negative_float(longest_pause_seconds) if vad_available else None
    word_count = len(_extract_words(transcript))
    fillers = _count_fillers(transcript)
    words_per_minute, pace_status = _calculate_words_per_minute(
        word_count,
        speech_duration,
    )

    silence_duration = None
    if total_duration is not None and speech_duration is not None:
        silence_duration = max(total_duration - speech_duration, 0.0)

    analysis_status = "available"
    if not vad_available or total_duration is None or speech_duration is None:
        analysis_status = "partial"

    return {
        "analysis_status": analysis_status,
        "source": "raw_audio_faster_whisper",
        "duration_seconds": round(total_duration, 1) if total_duration is not None else None,
        "speech_duration_seconds": round(speech_duration, 1) if speech_duration is not None else None,
        "silence_duration_seconds": round(silence_duration, 1) if silence_duration is not None else None,
        "word_count": word_count,
        "recognized_word_count": word_count,
        "words_per_minute": words_per_minute,
        "pace_status": pace_status,
        "pause_count": observed_pause_count,
        "longest_pause_seconds": round(longest_pause, 1) if longest_pause is not None else None,
        "pause_status": "available" if vad_available else "unavailable",
        "filler_word_count": sum(fillers.values()),
        "filler_words": fillers,
        "filler_status": "available",
        "meaningful_pause_threshold_seconds": MIN_MEANINGFUL_PAUSE_SECONDS,
    }


def unavailable_delivery(*, browser_duration_seconds=None, reason="unavailable"):
    """Represent analysis failure without turning unknown values into zeroes."""

    browser_duration = _non_negative_float(browser_duration_seconds)
    return {
        "analysis_status": "unavailable",
        "source": "raw_audio_processing_unavailable",
        "unavailable_reason": reason,
        "duration_seconds": None,
        "speech_duration_seconds": None,
        "silence_duration_seconds": None,
        "word_count": None,
        "recognized_word_count": None,
        "words_per_minute": None,
        "pace_status": "unavailable",
        "pause_count": None,
        "longest_pause_seconds": None,
        "pause_status": "unavailable",
        "filler_word_count": None,
        "filler_words": {},
        "filler_status": "unavailable",
        "meaningful_pause_threshold_seconds": MIN_MEANINGFUL_PAUSE_SECONDS,
        "browser_duration_seconds": round(browser_duration, 1) if browser_duration is not None else None,
    }
