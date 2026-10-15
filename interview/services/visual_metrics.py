from __future__ import annotations

import json
import math


SCHEMA_VERSION = "careergps-visual-v2"
LEGACY_SCHEMA_VERSION = "careergps-visual-v1"
MEDIAPIPE_VERSION = "1.0.1"
MAX_VISUAL_PAYLOAD_BYTES = 20 * 1024

VALID_STATUSES = {"complete", "partial", "unavailable", "error"}
VALID_UNAVAILABLE_REASONS = {
    "camera_permission_denied",
    "camera_not_found",
    "camera_disconnected",
    "camera_unavailable",
    "candidate_continued_audio_only",
    "model_load_failed",
    "runtime_unavailable",
    "worker_failed",
    "insufficient_coverage",
    "analysis_error",
}

TOP_LEVEL_KEYS = {
    "schema_version",
    "mediapipe_version",
    "status",
    "unavailable_reason",
    "duration_seconds",
    "frames_sampled",
    "pose_frames",
    "face_frames",
    "inference_ms_total",
    "posture",
    "movement",
    "gestures",
    "head_orientation",
    "framing",
}

GROUP_SCHEMAS = {
    "posture": {
        "baseline_frames": ("integer", 0, 10_000),
        "stable_fraction": ("number_or_null", 0, 1),
        "sustained_lean_events": ("integer", 0, 10_000),
        "large_shift_events": ("integer", 0, 10_000),
        "mean_shoulder_tilt_degrees": ("number_or_null", 0, 90),
    },
    "movement": {
        "mean_normalized_displacement": ("number_or_null", 0, 10),
        "significant_events": ("integer", 0, 10_000),
    },
    "gestures": {
        "visible_fraction": ("number_or_null", 0, 1),
        "event_count": ("integer", 0, 10_000),
        "mean_normalized_range": ("number_or_null", 0, 10),
    },
    "head_orientation": {
        "oriented_fraction": ("number_or_null", 0, 1),
        "sustained_away_events": ("integer", 0, 10_000),
        "mean_absolute_yaw_degrees": ("number_or_null", 0, 180),
        "mean_absolute_pitch_degrees": ("number_or_null", 0, 180),
        "mean_absolute_roll_degrees": ("number_or_null", 0, 180),
    },
    "framing": {
        "adequate_fraction": ("number_or_null", 0, 1),
        "outside_frame_frames": ("integer", 0, 60_000),
        "missing_landmark_frames": ("integer", 0, 60_000),
        "camera_instability_events": ("integer", 0, 10_000),
    },
}


class VisualMetricsValidationError(ValueError):
    pass


def _number(value, label, minimum, maximum, *, integer=False, nullable=False):
    if nullable and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise VisualMetricsValidationError(f"{label} must be a number.")
    if not math.isfinite(value) or value < minimum or value > maximum:
        raise VisualMetricsValidationError(f"{label} is outside the allowed range.")
    if integer and not isinstance(value, int):
        raise VisualMetricsValidationError(f"{label} must be a whole number.")
    return value


def _validate_group(name, value):
    if not isinstance(value, dict):
        raise VisualMetricsValidationError(f"{name} must be an object.")
    schema = GROUP_SCHEMAS[name]
    unknown = set(value) - set(schema)
    if unknown:
        raise VisualMetricsValidationError(f"{name} contains unsupported fields.")
    cleaned = {}
    for key, field in schema.items():
        if key not in value:
            raise VisualMetricsValidationError(f"{name}.{key} is required.")
        kind, minimum, maximum = field
        cleaned[key] = _number(
            value[key],
            f"{name}.{key}",
            minimum,
            maximum,
            integer=kind == "integer",
            nullable=kind == "number_or_null",
        )
    return cleaned


def _validate_visual_metrics_v1(raw_value, *, browser_duration=None):
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise VisualMetricsValidationError("Camera analysis data is required.")
    if len(raw_value.encode("utf-8")) > MAX_VISUAL_PAYLOAD_BYTES:
        raise VisualMetricsValidationError("Camera analysis data is too large.")
    try:
        data = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise VisualMetricsValidationError("Camera analysis data is not valid JSON.") from exc
    if not isinstance(data, dict):
        raise VisualMetricsValidationError("Camera analysis data must be an object.")
    if set(data) - TOP_LEVEL_KEYS:
        raise VisualMetricsValidationError("Camera analysis data contains unsupported fields.")
    if data.get("schema_version") != LEGACY_SCHEMA_VERSION:
        raise VisualMetricsValidationError("Camera analysis schema version is unsupported.")
    if data.get("mediapipe_version") != MEDIAPIPE_VERSION:
        raise VisualMetricsValidationError("MediaPipe runtime version is unsupported.")
    status = data.get("status")
    if status not in VALID_STATUSES:
        raise VisualMetricsValidationError("Camera analysis status is invalid.")

    duration = _number(data.get("duration_seconds"), "duration_seconds", 0, 7_200)
    frames = _number(data.get("frames_sampled"), "frames_sampled", 0, 60_000, integer=True)
    pose_frames = _number(data.get("pose_frames"), "pose_frames", 0, 60_000, integer=True)
    face_frames = _number(data.get("face_frames"), "face_frames", 0, 60_000, integer=True)
    inference_ms = _number(
        data.get("inference_ms_total"),
        "inference_ms_total",
        0,
        7_200_000,
    )
    if pose_frames > frames or face_frames > frames:
        raise VisualMetricsValidationError("Detected frame counts cannot exceed sampled frames.")
    if browser_duration is not None and browser_duration >= 0:
        tolerance = max(3.0, browser_duration * 0.25)
        if abs(duration - browser_duration) > tolerance:
            raise VisualMetricsValidationError("Camera and audio durations do not describe the same answer interval.")

    reason = data.get("unavailable_reason")
    if reason is not None and reason not in VALID_UNAVAILABLE_REASONS:
        raise VisualMetricsValidationError("Camera analysis failure reason is invalid.")

    groups = {}
    for name in GROUP_SCHEMAS:
        groups[name] = _validate_group(name, data.get(name))

    if groups["posture"]["baseline_frames"] > 60:
        raise VisualMetricsValidationError("Calibration frame count is invalid.")
    bounded_counts = (
        (groups["posture"]["sustained_lean_events"], pose_frames),
        (groups["posture"]["large_shift_events"], pose_frames),
        (groups["movement"]["significant_events"], pose_frames),
        (groups["gestures"]["event_count"], pose_frames),
        (groups["head_orientation"]["sustained_away_events"], face_frames),
        (groups["framing"]["outside_frame_frames"], frames),
        (groups["framing"]["missing_landmark_frames"], frames),
        (groups["framing"]["camera_instability_events"], pose_frames),
    )
    if any(value > ceiling for value, ceiling in bounded_counts):
        raise VisualMetricsValidationError("Camera analysis event counts are inconsistent with frame coverage.")

    if status in {"complete", "partial"}:
        if not frames or not pose_frames:
            raise VisualMetricsValidationError("Completed camera analysis requires sampled pose frames.")
        if reason and status == "complete":
            raise VisualMetricsValidationError("Complete camera analysis cannot include a failure reason.")
    else:
        if reason is None:
            raise VisualMetricsValidationError("Unavailable camera analysis requires a reason.")
        if frames or pose_frames or face_frames:
            raise VisualMetricsValidationError("Unavailable camera analysis cannot include analysed frames.")
        if any(
            value not in {0, None}
            for group in groups.values()
            for value in group.values()
        ):
            raise VisualMetricsValidationError("Unavailable camera analysis cannot include observations.")

    cleaned = {
        "schema_version": LEGACY_SCHEMA_VERSION,
        "mediapipe_version": MEDIAPIPE_VERSION,
        "status": status,
        "duration_seconds": round(duration, 3),
        "frames_sampled": frames,
        "pose_frames": pose_frames,
        "face_frames": face_frames,
        "inference_ms_total": round(inference_ms, 2),
        **groups,
    }
    if reason:
        cleaned["unavailable_reason"] = reason
    cleaned["analysis_quality"] = _analysis_quality(cleaned)
    cleaned["coaching"] = build_visual_coaching(cleaned)
    return cleaned


V2_TOP_LEVEL_KEYS = {
    "schema_version", "mediapipe_version", "status", "unavailable_reason",
    "duration_seconds", "frames_sampled", "pose_frames", "face_frames",
    "inference_ms_total", "coverage", "head_orientation", "framing",
    "shoulders", "hips", "gestures",
}
V2_GROUP_SCHEMAS = {
    "head_orientation": {
        "oriented_fraction": ("number", 0, 1),
        "sustained_away_events": ("integer", 0, 60_000),
        "mean_absolute_yaw_degrees": ("number", 0, 180),
        "mean_absolute_pitch_degrees": ("number", 0, 180),
        "mean_absolute_roll_degrees": ("number", 0, 90),
        "stable_position_fraction": ("number", 0, 1),
        "significant_position_events": ("integer", 0, 60_000),
    },
    "framing": {
        "adequate_fraction": ("number", 0, 1),
        "outside_frame_frames": ("integer", 0, 60_000),
    },
    "shoulders": {
        "mean_shoulder_tilt_degrees": ("number", 0, 90),
        "stable_fraction": ("number", 0, 1),
        "significant_events": ("integer", 0, 60_000),
    },
    "hips": {
        "mean_relative_movement": ("number_or_null", 0, 10),
        "significant_events": ("integer", 0, 60_000),
    },
    "gestures": {
        "event_count": ("integer", 0, 60_000),
        "mean_normalized_range": ("number_or_null", 0, 10),
    },
}
OPTIONAL_SIGNAL_FRAMES = {
    "shoulders": "shoulder_frames",
    "hips": "hip_frames",
    "gestures": "wrist_frames",
}


def _v2_group(name, value):
    schema = V2_GROUP_SCHEMAS[name]
    if not isinstance(value, dict) or set(value) != set(schema):
        raise VisualMetricsValidationError(f"{name} fields are invalid.")
    return {
        key: _number(
            value[key], f"{name}.{key}", minimum, maximum,
            integer=kind == "integer", nullable=kind == "number_or_null",
        )
        for key, (kind, minimum, maximum) in schema.items()
    }


def _signal_status(count, sampled):
    if count == 0:
        return "unavailable"
    if count < max(3, math.ceil(sampled * 0.35)):
        return "low_coverage"
    return "available"


def validate_visual_metrics(raw_value, *, browser_duration=None):
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise VisualMetricsValidationError("Camera analysis data is required.")
    if len(raw_value.encode("utf-8")) > MAX_VISUAL_PAYLOAD_BYTES:
        raise VisualMetricsValidationError("Camera analysis data is too large.")
    try:
        data = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise VisualMetricsValidationError("Camera analysis data is not valid JSON.") from exc
    if not isinstance(data, dict):
        raise VisualMetricsValidationError("Camera analysis data must be an object.")
    if data.get("schema_version") == LEGACY_SCHEMA_VERSION:
        return _validate_visual_metrics_v1(raw_value, browser_duration=browser_duration)
    if data.get("schema_version") != SCHEMA_VERSION:
        raise VisualMetricsValidationError("Camera analysis schema version is unsupported.")
    if set(data) - V2_TOP_LEVEL_KEYS:
        raise VisualMetricsValidationError("Camera analysis data contains unsupported fields.")
    if data.get("mediapipe_version") != MEDIAPIPE_VERSION:
        raise VisualMetricsValidationError("MediaPipe runtime version is unsupported.")
    status = data.get("status")
    if status not in VALID_STATUSES:
        raise VisualMetricsValidationError("Camera analysis status is invalid.")

    duration = _number(data.get("duration_seconds"), "duration_seconds", 0, 7_200)
    sampled = _number(data.get("frames_sampled"), "frames_sampled", 0, 60_000, integer=True)
    pose = _number(data.get("pose_frames"), "pose_frames", 0, sampled, integer=True)
    head = _number(data.get("face_frames"), "face_frames", 0, sampled, integer=True)
    inference = _number(data.get("inference_ms_total"), "inference_ms_total", 0, 7_200_000)
    if browser_duration is not None and browser_duration >= 0:
        if abs(duration - browser_duration) > max(3.0, browser_duration * 0.25):
            raise VisualMetricsValidationError("Camera and audio durations do not describe the same answer interval.")

    reason = data.get("unavailable_reason")
    if reason is not None and reason not in VALID_UNAVAILABLE_REASONS:
        raise VisualMetricsValidationError("Camera analysis failure reason is invalid.")
    coverage = data.get("coverage")
    if not isinstance(coverage, dict) or set(coverage) != {"shoulder_frames", "hip_frames", "wrist_frames"}:
        raise VisualMetricsValidationError("Camera analysis coverage fields are invalid.")
    coverage = {
        key: _number(coverage[key], f"coverage.{key}", 0, sampled, integer=True)
        for key in ("shoulder_frames", "hip_frames", "wrist_frames")
    }
    if any(count > pose for count in coverage.values()):
        raise VisualMetricsValidationError("Optional landmark coverage cannot exceed pose coverage.")

    groups = {}
    if head:
        if status in {"unavailable", "error"}:
            raise VisualMetricsValidationError("Detected head frames require partial or complete analysis.")
        groups["head_orientation"] = _v2_group("head_orientation", data.get("head_orientation"))
        groups["framing"] = _v2_group("framing", data.get("framing"))
        if groups["head_orientation"]["sustained_away_events"] > head or \
                groups["head_orientation"]["significant_position_events"] > head or \
                groups["framing"]["outside_frame_frames"] > head:
            raise VisualMetricsValidationError("Head event counts exceed head coverage.")
    else:
        if status in {"complete", "partial"} or "head_orientation" in data or "framing" in data:
            raise VisualMetricsValidationError("Head measurements require valid head frames.")

    if status == "complete":
        if reason is not None or head < max(3, math.ceil(sampled * 0.65)):
            raise VisualMetricsValidationError("Complete analysis requires sufficient head coverage without a failure.")
    elif status == "partial":
        if not head:
            raise VisualMetricsValidationError("Partial analysis requires valid head samples.")
    elif reason is None:
        raise VisualMetricsValidationError("Unavailable camera analysis requires a reason.")

    signal_coverage = {"head": {"frames": head, "status": _signal_status(head, sampled)}}
    for name, frame_key in OPTIONAL_SIGNAL_FRAMES.items():
        count = coverage[frame_key]
        signal_status = _signal_status(count, sampled)
        signal_coverage[name] = {"frames": count, "status": signal_status}
        if name in data:
            if not head or signal_status != "available":
                raise VisualMetricsValidationError(f"{name} measurements lack sufficient coverage.")
            groups[name] = _v2_group(name, data[name])
            event_key = "event_count" if name == "gestures" else "significant_events"
            if groups[name][event_key] > count:
                raise VisualMetricsValidationError(f"{name} events exceed landmark coverage.")
        elif head and signal_status == "available":
            raise VisualMetricsValidationError(f"{name} measurements are required for available coverage.")

    cleaned = {
        "schema_version": SCHEMA_VERSION,
        "mediapipe_version": MEDIAPIPE_VERSION,
        "status": status,
        "duration_seconds": round(duration, 3),
        "frames_sampled": sampled,
        "pose_frames": pose,
        "face_frames": head,
        "inference_ms_total": round(inference, 2),
        "coverage": coverage,
        "signal_coverage": signal_coverage,
        **groups,
    }
    if reason:
        cleaned["unavailable_reason"] = reason
    cleaned["analysis_quality"] = _analysis_quality(cleaned)
    cleaned["coaching"] = build_visual_coaching(cleaned)
    return cleaned


def _analysis_quality(metrics):
    if metrics["status"] in {"unavailable", "error"}:
        return "unavailable"
    duration = metrics["duration_seconds"]
    frames = metrics["frames_sampled"]
    signal_frames = metrics["face_frames"] if metrics.get("schema_version") == SCHEMA_VERSION else metrics["pose_frames"]
    coverage = signal_frames / frames if frames else 0
    if duration >= 15 and frames >= 40 and coverage >= 0.75:
        return "high"
    if duration >= 5 and frames >= 12 and coverage >= 0.45:
        return "medium"
    return "low"


def build_visual_coaching(metrics):
    if metrics.get("schema_version") == SCHEMA_VERSION:
        return _build_visual_coaching_v2(metrics)
    if metrics["status"] in {"unavailable", "error"}:
        return {
            "summary": "Camera / Body Analysis was not available for this answer.",
            "observations": [],
            "practice_tips": [],
        }
    if metrics["analysis_quality"] == "low":
        return {
            "summary": "Camera / Body Analysis had limited coverage, so use these observations cautiously.",
            "observations": [],
            "practice_tips": ["Keep your shoulders, head, and hands inside the preview for more complete practice feedback."],
        }

    observations = []
    tips = []
    posture = metrics["posture"]
    movement = metrics["movement"]
    gestures = metrics["gestures"]
    head = metrics["head_orientation"]
    framing = metrics["framing"]

    if posture["stable_fraction"] is not None:
        if posture["stable_fraction"] >= 0.75:
            observations.append("Your upper-body position stayed fairly consistent through this answer.")
        elif posture["sustained_lean_events"]:
            tips.append("Practise returning to a comfortable upright position after leaning to either side.")
    if movement["significant_events"] >= 4:
        tips.append("Try reducing repeated large upper-body shifts while keeping natural movement.")
    elif movement["mean_normalized_displacement"] is not None:
        observations.append("Your upper-body movement stayed within a moderate range.")
    if gestures["visible_fraction"] is not None:
        if gestures["visible_fraction"] < 0.35:
            tips.append("If you use hand gestures, keep them within the camera frame so you can review them.")
        elif gestures["event_count"]:
            observations.append("Visible hand movement accompanied parts of your answer.")
    if head["oriented_fraction"] is not None:
        if head["oriented_fraction"] >= 0.7:
            observations.append("Your head was generally oriented toward the camera.")
        elif head["sustained_away_events"]:
            tips.append("Practise keeping your head generally oriented toward the interviewer or camera while thinking.")
    if framing["adequate_fraction"] is not None and framing["adequate_fraction"] < 0.7:
        tips.append("Adjust the camera so your head, shoulders, and hands remain visible for more of the answer.")
    if framing["camera_instability_events"]:
        tips.append("Place the camera on a stable surface before starting your next practice answer.")

    return {
        "summary": "Camera / Body Analysis is based on observable movement and framing during this answer.",
        "observations": observations[:4],
        "practice_tips": tips[:4],
    }


def _build_visual_coaching_v2(metrics):
    if metrics["status"] in {"unavailable", "error"}:
        return {
            "summary": "Visual Interview Coaching was not available for this answer.",
            "observations": [],
            "practice_tips": ["Keep your face visible inside the preview during your next practice answer."],
        }
    if metrics["analysis_quality"] == "low":
        return {
            "summary": "Visual Interview Coaching had limited head coverage; use these observations cautiously.",
            "observations": [],
            "practice_tips": ["Keep your face visible inside the preview for more complete feedback."],
        }

    head = metrics["head_orientation"]
    framing = metrics["framing"]
    observations = []
    tips = []
    if head["oriented_fraction"] >= 0.7:
        observations.append("Your head was generally oriented toward the camera during this answer.")
    elif head["sustained_away_events"]:
        tips.append("Practise returning your head toward the camera after a sustained turn.")
    if head["stable_position_fraction"] >= 0.7:
        observations.append("Your head position stayed fairly consistent within the frame.")
    elif head["significant_position_events"] >= 3:
        tips.append("Try limiting repeated large head-position changes while speaking naturally.")
    if framing["adequate_fraction"] < 0.7:
        tips.append("Adjust the camera so your face stays inside the preview for more of the answer.")

    shoulders = metrics.get("shoulders")
    if shoulders:
        if shoulders["stable_fraction"] >= 0.7:
            observations.append("Visible shoulder position stayed fairly consistent.")
        elif shoulders["significant_events"] >= 3:
            tips.append("Practise reducing repeated large shoulder-position shifts if they distract from your answer.")
    hips = metrics.get("hips")
    if hips and hips["significant_events"] >= 3:
        tips.append("Try keeping larger torso shifts deliberate during a video interview.")
    gestures = metrics.get("gestures")
    if gestures and gestures["event_count"]:
        observations.append("Visible hand movement accompanied parts of your answer.")

    return {
        "summary": "Visual Interview Coaching uses approximate head orientation and other visible geometry only.",
        "observations": observations[:4],
        "practice_tips": tips[:4],
    }


def build_overall_visual_summary(interview, questions):
    if interview.mode != "voice":
        return None
    label = (
        "Visual Interview Coaching"
        if interview.visual_analysis_version == SCHEMA_VERSION
        else "Camera / Body Analysis"
    )
    if interview.camera_mode != "camera_analysis":
        return {
            "status": "not_requested",
            "message": f"{label} was not requested for this interview.",
            "observations": [],
            "practice_tips": [],
        }
    available = [
        question.visual_metrics
        for question in questions
        if question.visual_analysis_status in {"complete", "partial"}
        and question.visual_metrics
    ]
    if not available:
        return {
            "status": "unavailable",
            "message": f"{label} was not available for this interview.",
            "observations": [],
            "practice_tips": [],
        }
    observations = []
    tips = []
    for metrics in available:
        coaching = metrics.get("coaching", {})
        observations.extend(coaching.get("observations", []))
        tips.extend(coaching.get("practice_tips", []))
    return {
        "status": "available",
        "message": f"{label} was available for {len(available)} of {len(questions)} answers.",
        "observations": list(dict.fromkeys(observations))[:4],
        "practice_tips": list(dict.fromkeys(tips))[:4],
    }
