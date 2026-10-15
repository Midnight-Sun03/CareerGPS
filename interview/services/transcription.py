from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from tempfile import NamedTemporaryFile

from django.conf import settings

from .delivery_metrics import MIN_MEANINGFUL_PAUSE_SECONDS, analyse_delivery


class TranscriptionUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class AuthoritativeVoiceResult:
    transcript: str
    word_timestamps: list[dict]
    delivery_metrics: dict
    vad_available: bool


@lru_cache(maxsize=1)
def get_transcription_model():
    """Load one reusable faster-whisper model per application process."""

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscriptionUnavailable(
            "The faster-whisper runtime is not installed."
        ) from exc

    model_path = str(
        getattr(settings, "INTERVIEW_WHISPER_MODEL_PATH", "")
        or getattr(settings, "INTERVIEW_WHISPER_MODEL", "base.en")
    )
    try:
        return WhisperModel(
            model_path,
            device=getattr(settings, "INTERVIEW_WHISPER_DEVICE", "cpu"),
            compute_type=getattr(settings, "INTERVIEW_WHISPER_COMPUTE_TYPE", "int8"),
            local_files_only=getattr(
                settings,
                "INTERVIEW_WHISPER_LOCAL_FILES_ONLY",
                True,
            ),
        )
    except Exception as exc:
        raise TranscriptionUnavailable(
            "The configured transcription model is unavailable."
        ) from exc


def _materialize_audio(file_field):
    suffix = Path(file_field.name or "answer.webm").suffix or ".webm"
    temporary = NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        file_field.open("rb")
        for chunk in iter(lambda: file_field.read(1024 * 1024), b""):
            temporary.write(chunk)
        temporary.close()
        return Path(temporary.name)
    except Exception:
        temporary.close()
        Path(temporary.name).unlink(missing_ok=True)
        raise
    finally:
        try:
            file_field.close()
        except Exception:
            pass


def _segments_to_evidence(segments):
    segment_rows = []
    words = []
    text_parts = []
    for segment in segments:
        start = max(float(segment.start), 0.0)
        end = max(float(segment.end), start)
        segment_rows.append((start, end))
        text_parts.append(str(segment.text or "").strip())
        for word in segment.words or []:
            words.append({
                "word": str(word.word or "").strip(),
                "start": round(max(float(word.start or 0), 0.0), 3),
                "end": round(max(float(word.end or 0), 0.0), 3),
                "probability": round(float(word.probability or 0), 4),
            })
    return " ".join(part for part in text_parts if part).strip(), words, segment_rows


def _timing_from_segments(segment_rows):
    speech_duration = sum(max(end - start, 0.0) for start, end in segment_rows)
    pauses = [
        max(current[0] - previous[1], 0.0)
        for previous, current in zip(segment_rows, segment_rows[1:])
    ]
    meaningful = [
        pause for pause in pauses if pause >= MIN_MEANINGFUL_PAUSE_SECONDS
    ]
    return speech_duration, len(meaningful), max(meaningful, default=0.0)


def _silero_speech_intervals(audio_path):
    """Return the exact speech intervals produced by faster-whisper's VAD."""

    from faster_whisper.audio import decode_audio
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    sampling_rate = 16000
    audio = decode_audio(str(audio_path), sampling_rate=sampling_rate)
    chunks = get_speech_timestamps(
        audio,
        VadOptions(
            min_silence_duration_ms=int(MIN_MEANINGFUL_PAUSE_SECONDS * 1000)
        ),
    )
    return [
        (chunk["start"] / sampling_rate, chunk["end"] / sampling_rate)
        for chunk in chunks
    ]


def _transcribe(model, audio_path, *, use_vad):
    kwargs = {
        "beam_size": 5,
        "word_timestamps": True,
        "vad_filter": use_vad,
    }
    if use_vad:
        kwargs["vad_parameters"] = {
            "min_silence_duration_ms": int(MIN_MEANINGFUL_PAUSE_SECONDS * 1000)
        }
    segments, info = model.transcribe(str(audio_path), **kwargs)
    return list(segments), info


def transcribe_answer_audio(file_field):
    """Transcribe saved candidate audio and derive authoritative metrics."""

    model = get_transcription_model()
    audio_path = _materialize_audio(file_field)
    try:
        try:
            speech_intervals = _silero_speech_intervals(audio_path)
            vad_available = True
        except Exception:
            vad_available = False
            speech_intervals = []

        try:
            segments, info = _transcribe(
                model,
                audio_path,
                use_vad=vad_available,
            )
        except Exception:
            # If integrated VAD transcription failed, preserve the transcript
            # where possible and mark VAD-derived measurements unavailable.
            vad_available = False
            try:
                segments, info = _transcribe(model, audio_path, use_vad=False)
            except Exception as exc:
                raise TranscriptionUnavailable(
                    "The saved audio could not be transcribed."
                ) from exc

        transcript, words, _ = _segments_to_evidence(segments)
        speech_duration, pause_count, longest_pause = _timing_from_segments(
            speech_intervals
        )
        metrics = analyse_delivery(
            transcript,
            duration_seconds=getattr(info, "duration", None),
            speech_duration_seconds=speech_duration if vad_available else None,
            pause_count=pause_count if vad_available else None,
            longest_pause_seconds=longest_pause if vad_available else None,
            vad_available=vad_available,
        )
        return AuthoritativeVoiceResult(
            transcript=transcript,
            word_timestamps=words,
            delivery_metrics=metrics,
            vad_available=vad_available,
        )
    finally:
        audio_path.unlink(missing_ok=True)
