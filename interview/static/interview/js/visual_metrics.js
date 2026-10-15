export const VISUAL_SCHEMA_VERSION = "careergps-visual-v2";
export const MEDIAPIPE_VERSION = "1.0.1";

const clamp = (value, minimum, maximum) => Math.min(maximum, Math.max(minimum, value));
const round = (value, places = 3) => Number(value.toFixed(places));
const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
const midpoint = (a, b) => ({x: (a.x + b.x) / 2, y: (a.y + b.y) / 2});
const mean = values => values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
const lineAngleDifference = (angle, baseline) => {
    const delta = ((angle - baseline + 90) % 180 + 180) % 180 - 90;
    return Math.abs(delta);
};

function validPoint(point, requireVisibility = false) {
    return Boolean(point && Number.isFinite(point.x) && Number.isFinite(point.y)
        && point.x >= -1 && point.x <= 2 && point.y >= -1 && point.y <= 2
        && (!requireVisibility || (Number.isFinite(point.visibility) && point.visibility >= 0.45)));
}

function inFrame(point) {
    return point.x >= 0.03 && point.x <= 0.97 && point.y >= 0.03 && point.y <= 0.97;
}

function headGeometry(face) {
    if (!face) return null;
    const required = [face.nose, face.leftEye, face.rightEye, face.forehead, face.chin];
    if (!required.every(point => validPoint(point))) return null;
    const eyeMid = midpoint(face.leftEye, face.rightEye);
    const eyeWidth = distance(face.leftEye, face.rightEye);
    const height = distance(face.forehead, face.chin);
    if (eyeWidth < 0.015 || height < 0.05) return null;
    return {
        centre: midpoint(face.forehead, face.chin),
        width: eyeWidth,
        framed: required.every(inFrame),
        yaw: clamp(((face.nose.x - eyeMid.x) / eyeWidth) * 50, -180, 180),
        pitch: clamp(((face.nose.y - eyeMid.y) / height) * 90, -180, 180),
        // Use the unsigned horizontal span so mirrored/reversed eye labels
        // cannot turn a small physical tilt into an angle near 180 degrees.
        roll: Math.atan2(face.rightEye.y - face.leftEye.y,
            Math.abs(face.rightEye.x - face.leftEye.x)) * 180 / Math.PI,
    };
}

function shoulderGeometry(pose) {
    if (!pose || !validPoint(pose.leftShoulder, true) || !validPoint(pose.rightShoulder, true)) return null;
    const left = pose.leftShoulder;
    const right = pose.rightShoulder;
    const width = distance(left, right);
    if (width < 0.02) return null;
    return {
        centre: midpoint(left, right),
        width,
        // Both axes are absolute: this is an acute angle to the horizontal
        // and always satisfies the server's physically sensible 0-90 range.
        tilt: Math.atan2(Math.abs(right.y - left.y), Math.abs(right.x - left.x)) * 180 / Math.PI,
    };
}

function hipGeometry(pose) {
    if (!pose || !validPoint(pose.leftHip, true) || !validPoint(pose.rightHip, true)) return null;
    if (distance(pose.leftHip, pose.rightHip) < 0.02) return null;
    return {centre: midpoint(pose.leftHip, pose.rightHip)};
}

function wristGeometry(pose) {
    if (!pose) return null;
    const left = validPoint(pose.leftWrist, true) ? pose.leftWrist : null;
    const right = validPoint(pose.rightWrist, true) ? pose.rightWrist : null;
    return left || right ? {left, right} : null;
}

function geometry(observation) {
    return {
        head: headGeometry(observation.face),
        shoulders: shoulderGeometry(observation.pose),
        hips: hipGeometry(observation.pose),
        wrists: wristGeometry(observation.pose),
    };
}

function signalAvailable(count, sampled) {
    return count >= Math.max(3, Math.ceil(sampled * 0.35));
}

export function unavailableVisualMetrics(durationSeconds, reason, status = "unavailable") {
    return {
        schema_version: VISUAL_SCHEMA_VERSION,
        mediapipe_version: MEDIAPIPE_VERSION,
        status,
        unavailable_reason: reason,
        duration_seconds: round(Math.max(0, durationSeconds || 0)),
        frames_sampled: 0,
        pose_frames: 0,
        face_frames: 0,
        inference_ms_total: 0,
        coverage: {shoulder_frames: 0, hip_frames: 0, wrist_frames: 0},
    };
}

export class VisualMetricsAccumulator {
    constructor() {
        this.calibration = [];
        this.reset();
    }

    reset() {
        this.active = false;
        this.failureReason = null;
        this.frames = 0;
        this.poseFrames = 0;
        this.faceFrames = 0;
        this.shoulderFrames = 0;
        this.hipFrames = 0;
        this.wristFrames = 0;
        this.inferenceMs = 0;
        this.headFramedFrames = 0;
        this.headOrientedFrames = 0;
        this.headStableFrames = 0;
        this.headAwayRun = 0;
        this.headAwayEvents = 0;
        this.headShiftEvents = 0;
        this.shoulderStableFrames = 0;
        this.shoulderShiftEvents = 0;
        this.hipShiftEvents = 0;
        this.gestureEvents = 0;
        this.yaws = [];
        this.pitches = [];
        this.rolls = [];
        this.shoulderTilts = [];
        this.hipMovements = [];
        this.gestureRanges = [];
        this.previousHead = null;
        this.previousShoulders = null;
        this.previousHips = null;
        this.previousWrists = null;
        this.shoulderBaseline = null;
    }

    addCalibration(observation) {
        const sample = geometry(observation);
        if (sample.head?.framed && this.calibration.length < 60) this.calibration.push(sample);
        return {
            faceDetected: Boolean(observation.face),
            headUsable: Boolean(sample.head),
            adequateFraming: Boolean(sample.head?.framed),
            analysisReady: Boolean(sample.head?.framed),
            headFrames: this.calibration.length,
        };
    }

    start() {
        const calibration = this.calibration;
        this.reset();
        this.calibration = calibration;
        this.shoulderBaseline = mean(calibration.filter(item => item.shoulders)
            .map(item => item.shoulders.tilt));
        this.active = true;
    }

    fail(reason) {
        this.failureReason = reason;
        this.active = false;
    }

    add(observation) {
        if (!this.active) return;
        this.frames += 1;
        const inference = Number(observation.inferenceMs);
        if (Number.isFinite(inference)) this.inferenceMs += clamp(inference, 0, 10_000);
        if (observation.pose) this.poseFrames += 1;

        const sample = geometry(observation);
        const head = sample.head;
        const shoulders = sample.shoulders;
        const hips = sample.hips;
        const wrists = sample.wrists;
        if (!head) {
            this.headAwayRun = 0;
            this.previousHead = null;
            this.previousHips = null;
            this.previousWrists = null;
        } else {
            this.faceFrames += 1;
            if (head.framed) this.headFramedFrames += 1;
            const baseline = this.#headBaseline();
            const yaw = clamp(Math.abs(head.yaw - baseline.yaw), 0, 180);
            const pitch = clamp(Math.abs(head.pitch - baseline.pitch), 0, 180);
            const roll = lineAngleDifference(head.roll, baseline.roll);
            this.yaws.push(yaw);
            this.pitches.push(pitch);
            this.rolls.push(roll);
            if (yaw <= 24 && pitch <= 22 && roll <= 20) {
                this.headOrientedFrames += 1;
                this.headAwayRun = 0;
            } else {
                this.headAwayRun += 1;
                if (this.headAwayRun === 3) this.headAwayEvents += 1;
            }
            if (this.previousHead) {
                const shift = distance(head.centre, this.previousHead.centre) / Math.max(0.04, head.width);
                if (shift <= 0.15) this.headStableFrames += 1;
                if (shift > 0.25) this.headShiftEvents += 1;
            } else {
                this.headStableFrames += 1;
            }
            this.previousHead = head;
        }

        if (shoulders) {
            this.shoulderFrames += 1;
            this.shoulderTilts.push(shoulders.tilt);
            if (this.shoulderBaseline === null) this.shoulderBaseline = shoulders.tilt;
            if (Math.abs(shoulders.tilt - this.shoulderBaseline) <= 12) this.shoulderStableFrames += 1;
            if (this.previousShoulders && head && this.previousShoulders.head) {
                const relativeNow = {x: shoulders.centre.x - head.centre.x, y: shoulders.centre.y - head.centre.y};
                const relativeBefore = this.previousShoulders.relative;
                if (distance(relativeNow, relativeBefore) / Math.max(0.04, head.width) > 0.25) {
                    this.shoulderShiftEvents += 1;
                }
            }
            this.previousShoulders = {
                head: Boolean(head),
                relative: head ? {x: shoulders.centre.x - head.centre.x, y: shoulders.centre.y - head.centre.y} : null,
            };
        } else {
            this.previousShoulders = null;
        }

        if (hips && head) {
            this.hipFrames += 1;
            const relative = {x: hips.centre.x - head.centre.x, y: hips.centre.y - head.centre.y};
            if (this.previousHips) {
                const movement = distance(relative, this.previousHips) / Math.max(0.04, head.width);
                this.hipMovements.push(clamp(movement, 0, 10));
                if (movement > 0.25) this.hipShiftEvents += 1;
            }
            this.previousHips = relative;
        } else {
            this.previousHips = null;
        }

        if (wrists && head) {
            this.wristFrames += 1;
            const ranges = [];
            if (this.previousWrists?.left && wrists.left) {
                ranges.push(distance(wrists.left, this.previousWrists.left) / Math.max(0.04, head.width));
            }
            if (this.previousWrists?.right && wrists.right) {
                ranges.push(distance(wrists.right, this.previousWrists.right) / Math.max(0.04, head.width));
            }
            if (ranges.length) {
                const range = mean(ranges);
                this.gestureRanges.push(clamp(range, 0, 10));
                if (range > 0.16) this.gestureEvents += 1;
            }
            this.previousWrists = wrists;
        } else {
            this.previousWrists = null;
        }
    }

    finish(durationSeconds) {
        this.active = false;
        const status = !this.faceFrames ? "unavailable"
            : !this.failureReason && this.faceFrames >= Math.max(3, Math.ceil(this.frames * 0.65))
                ? "complete" : "partial";
        const payload = {
            schema_version: VISUAL_SCHEMA_VERSION,
            mediapipe_version: MEDIAPIPE_VERSION,
            status,
            duration_seconds: round(Math.max(0, durationSeconds || 0)),
            frames_sampled: this.frames,
            pose_frames: this.poseFrames,
            face_frames: this.faceFrames,
            inference_ms_total: round(this.inferenceMs, 2),
            coverage: {
                shoulder_frames: this.shoulderFrames,
                hip_frames: this.hipFrames,
                wrist_frames: this.wristFrames,
            },
        };
        if (this.failureReason) payload.unavailable_reason = this.failureReason;
        else if (status === "unavailable") payload.unavailable_reason = "insufficient_coverage";

        if (this.faceFrames) {
            payload.head_orientation = {
                oriented_fraction: round(this.headOrientedFrames / this.faceFrames),
                sustained_away_events: this.headAwayEvents,
                mean_absolute_yaw_degrees: round(mean(this.yaws), 2),
                mean_absolute_pitch_degrees: round(mean(this.pitches), 2),
                mean_absolute_roll_degrees: round(mean(this.rolls), 2),
                stable_position_fraction: round(this.headStableFrames / this.faceFrames),
                significant_position_events: this.headShiftEvents,
            };
            payload.framing = {
                adequate_fraction: round(this.headFramedFrames / this.faceFrames),
                outside_frame_frames: this.faceFrames - this.headFramedFrames,
            };
            if (signalAvailable(this.shoulderFrames, this.frames)) {
                payload.shoulders = {
                    mean_shoulder_tilt_degrees: round(mean(this.shoulderTilts), 2),
                    stable_fraction: round(this.shoulderStableFrames / this.shoulderFrames),
                    significant_events: this.shoulderShiftEvents,
                };
            }
            if (signalAvailable(this.hipFrames, this.frames)) {
                payload.hips = {
                    mean_relative_movement: this.hipMovements.length ? round(mean(this.hipMovements)) : null,
                    significant_events: this.hipShiftEvents,
                };
            }
            if (signalAvailable(this.wristFrames, this.frames)) {
                payload.gestures = {
                    event_count: this.gestureEvents,
                    mean_normalized_range: this.gestureRanges.length ? round(mean(this.gestureRanges)) : null,
                };
            }
        }
        const calibration = this.calibration;
        this.reset();
        this.calibration = calibration;
        return payload;
    }

    #headBaseline() {
        const heads = this.calibration.map(item => item.head);
        return {
            yaw: mean(heads.map(item => item.yaw)) ?? 0,
            pitch: mean(heads.map(item => item.pitch)) ?? 0,
            roll: mean(heads.map(item => item.roll)) ?? 0,
        };
    }
}
