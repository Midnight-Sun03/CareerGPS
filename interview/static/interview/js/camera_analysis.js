import {VisualMetricsAccumulator, unavailableVisualMetrics} from "./visual_metrics.js";

const INITIALIZATION_INACTIVITY_TIMEOUT_MS = 60000;
const INITIALIZATION_TOTAL_TIMEOUT_MS = 150000;
const FIRST_FRAME_TIMEOUT_MS = 10000;
const READY_FRAME_STREAK = 3;

export class CameraAnalysisController {
    constructor(config) {
        this.config = config;
        this.video = config.video;
        this.status = config.status;
        this.readyButton = config.readyButton;
        this.accumulator = new VisualMetricsAccumulator();
        this.stream = null;
        this.worker = null;
        this.frameBusy = false;
        this.collecting = false;
        this.setupActive = true;
        this.samplingEnabled = false;
        this.disabledReason = null;
        this.isReady = false;
        this.timer = null;
        this.setupWatchdog = null;
        this.setupFrames = 0;
        this.readyFrameStreak = 0;
        this.cancelInitialization = null;
        this.closed = false;
    }

    async setup() {
        if (!navigator.mediaDevices?.getUserMedia || !("Worker" in window) || !("createImageBitmap" in window)) {
            this.#disable("runtime_unavailable", "Camera analysis is unavailable in this browser. You can continue with audio only.");
            return false;
        }
        this.status.textContent = "Requesting camera permission...";
        try {
            this.stream = await navigator.mediaDevices.getUserMedia({
                audio: false,
                video: {width: {ideal: 640}, height: {ideal: 480}, facingMode: "user"},
            });
            this.video.srcObject = this.stream;
            await this.video.play();
            await this.#waitForVideoDimensions();
            this.stream.getVideoTracks()[0].addEventListener("ended", () => {
                if (!this.closed) {
                    this.#disable("camera_disconnected", "The camera disconnected. Audio practice can continue.");
                }
            });
        } catch (error) {
            const reason = error?.name === "NotAllowedError" ? "camera_permission_denied"
                : error?.name === "NotFoundError" ? "camera_not_found" : "camera_unavailable";
            this.#disable(reason, "The camera could not be opened. Retry Camera Analysis or continue with audio only.");
            return false;
        }

        this.status.textContent = "Loading local camera-analysis models...";
        try {
            await this.#startWorker();
        } catch (error) {
            if (!this.disabledReason && !this.closed) {
                // Keep the candidate-facing message generic; the worker's
                // original detail is logged to the console for diagnosis.
                console.error(
                    "CareerGPS camera analysis initialization failed:",
                    error?.detail || error,
                );
                this.#disable("model_load_failed", "Camera-analysis models could not load. Retry Camera Analysis or continue with audio only.");
            }
            return false;
        }
        this.status.textContent = "Models ready. Analysing a real camera frame...";
        this.#startSampling();
        this.samplingEnabled = true;
        this.setupWatchdog = window.setTimeout(() => {
            if (this.setupActive && this.setupFrames === 0) {
                this.#disable(
                    "analysis_error",
                    "Camera detected, but no frame could be analysed. Retry Camera Analysis or continue with audio only.",
                );
            }
        }, FIRST_FRAME_TIMEOUT_MS);
        return true;
    }

    finishSetup() {
        this.setupActive = false;
        this.samplingEnabled = false;
        if (this.setupWatchdog) window.clearTimeout(this.setupWatchdog);
        this.setupWatchdog = null;
    }

    startAnswer(timestamp) {
        if (this.disabledReason || !this.isReady) return;
        this.accumulator.start(timestamp);
        this.collecting = true;
        this.samplingEnabled = true;
        this.status.textContent = "Visual Interview Coaching is collecting during your answer.";
    }

    finishAnswer(durationSeconds) {
        if (this.disabledReason && !this.accumulator.frames) {
            return unavailableVisualMetrics(durationSeconds, this.disabledReason, "unavailable");
        }
        this.collecting = false;
        this.samplingEnabled = false;
        const metrics = this.accumulator.finish(durationSeconds);
        this.status.textContent = "Visual Interview Coaching paused until your next answer.";
        return metrics;
    }

    continueAudioOnly() {
        this.#disable("candidate_continued_audio_only", "Continuing with audio only.");
        if (this.stream) this.stream.getTracks().forEach(track => track.stop());
        this.stream = null;
        this.video.srcObject = null;
        this.readyButton.disabled = false;
    }

    close() {
        this.closed = true;
        if (this.timer) window.clearInterval(this.timer);
        if (this.setupWatchdog) window.clearTimeout(this.setupWatchdog);
        if (this.cancelInitialization) this.cancelInitialization();
        if (this.worker) this.worker.terminate();
        if (this.stream) this.stream.getTracks().forEach(track => track.stop());
        this.worker = null;
        this.stream = null;
        this.video.srcObject = null;
    }

    #startWorker() {
        return new Promise((resolve, reject) => {
            // The pinned Emscripten loader exposes ModuleFactory through
            // importScripts(), so the inference worker must remain classic.
            this.worker = new Worker(this.config.workerUrl);
            let settled = false;
            let inactivityTimeout = null;
            const totalTimeout = window.setTimeout(
                () => failInitialization("Model setup exceeded the total time limit."),
                INITIALIZATION_TOTAL_TIMEOUT_MS,
            );
            const clearInitializationTimers = () => {
                window.clearTimeout(inactivityTimeout);
                window.clearTimeout(totalTimeout);
                this.cancelInitialization = null;
            };
            const failInitialization = (message, detail) => {
                if (settled) return;
                settled = true;
                clearInitializationTimers();
                if (this.worker) this.worker.terminate();
                this.worker = null;
                const error = new Error(message);
                // Carry the worker's original detail for console diagnostics
                // only; the candidate-facing message stays generic.
                error.detail = detail || message;
                reject(error);
            };
            const resetInactivityTimeout = () => {
                window.clearTimeout(inactivityTimeout);
                inactivityTimeout = window.setTimeout(
                    () => failInitialization("Model setup stopped making progress."),
                    INITIALIZATION_INACTIVITY_TIMEOUT_MS,
                );
            };
            this.cancelInitialization = () => failInitialization("Camera setup was cancelled.");
            resetInactivityTimeout();

            this.worker.onmessage = event => {
                if (this.closed) return;
                if (event.data.type === "ready") {
                    if (settled) return;
                    settled = true;
                    clearInitializationTimers();
                    resolve();
                    return;
                }
                if (event.data.type === "progress") {
                    if (settled) return;
                    resetInactivityTimeout();
                    const labels = {
                        runtime_ready: "Local camera-analysis runtime ready; loading pose model...",
                        pose_ready: "Pose model ready; loading head-orientation model...",
                        face_ready: "Camera-analysis models ready...",
                    };
                    this.status.textContent = labels[event.data.stage] || this.status.textContent;
                    return;
                }
                if (event.data.type === "frame_complete") {
                    this.frameBusy = false;
                    return;
                }
                if (event.data.type === "observation") {
                    if (this.collecting) {
                        this.accumulator.add(event.data);
                    } else if (this.setupActive) {
                        this.#handleSetupObservation(event.data);
                    }
                    return;
                }
                if (event.data.type === "error") {
                    if (!settled) {
                        failInitialization(
                            "Camera analysis worker failed during setup.",
                            event.data.detail,
                        );
                        return;
                    }
                    if (this.collecting) this.accumulator.fail(event.data.reason || "analysis_error");
                    this.#disable(event.data.reason || "worker_failed", "Camera analysis stopped. Retry Camera Analysis or continue with audio only.");
                }
            };
            this.worker.onerror = event => {
                if (this.closed) return;
                if (!settled) {
                    failInitialization(
                        "Camera analysis worker failed during setup.",
                        event?.message,
                    );
                    return;
                }
                this.#disable("worker_failed", "Camera analysis stopped. Retry Camera Analysis or continue with audio only.");
            };
            this.worker.postMessage({
                type: "init",
                bundleUrl: this.config.bundleUrl,
                wasmRootUrl: this.config.wasmRootUrl,
                poseModelUrl: this.config.poseModelUrl,
                faceModelUrl: this.config.faceModelUrl,
            });
        });
    }

    #handleSetupObservation(observation) {
        this.setupFrames += 1;
        if (this.setupWatchdog) window.clearTimeout(this.setupWatchdog);
        this.setupWatchdog = null;
        const coverage = this.accumulator.addCalibration(observation);
        if (this.isReady) return;

        this.readyFrameStreak = coverage.analysisReady ? this.readyFrameStreak + 1 : 0;
        if (this.readyFrameStreak >= READY_FRAME_STREAK) {
            this.isReady = true;
            this.disabledReason = null;
            this.status.textContent = "Camera Analysis READY. Your face is visible and well framed.";
            this.readyButton.disabled = false;
            if (this.config.onReady) this.config.onReady();
        } else if (!coverage.faceDetected) {
            this.status.textContent = "Camera detected. Move your face into the preview.";
        } else if (!coverage.headUsable) {
            this.status.textContent = "Camera detected. Keep your face clearly visible for head-landmark detection.";
        } else if (!coverage.adequateFraming) {
            this.status.textContent = "Camera detected. Reposition so your face stays inside the preview.";
        } else {
            this.status.textContent = "Camera detected. Hold this position while readiness is confirmed.";
        }
    }

    #waitForVideoDimensions() {
        if (this.video.videoWidth > 0 && this.video.videoHeight > 0) return Promise.resolve();
        return new Promise((resolve, reject) => {
            const ready = () => {
                if (this.video.videoWidth <= 0 || this.video.videoHeight <= 0) return;
                cleanup();
                resolve();
            };
            const timeout = window.setTimeout(() => {
                cleanup();
                reject(new Error("Camera preview did not become ready."));
            }, 5000);
            const cleanup = () => {
                window.clearTimeout(timeout);
                this.video.removeEventListener("loadeddata", ready);
                this.video.removeEventListener("resize", ready);
            };
            this.video.addEventListener("loadeddata", ready);
            this.video.addEventListener("resize", ready);
            ready();
        });
    }

    #startSampling() {
        this.timer = window.setInterval(async () => {
            if (!this.samplingEnabled || this.frameBusy || !this.worker || this.video.readyState < 2 || this.disabledReason) return;
            this.frameBusy = true;
            try {
                const bitmap = await createImageBitmap(this.video);
                if (!this.worker || this.disabledReason) {
                    bitmap.close();
                    this.frameBusy = false;
                    return;
                }
                this.worker.postMessage({type: "frame", bitmap, timestamp: performance.now()}, [bitmap]);
            } catch (_) {
                this.frameBusy = false;
                if (this.collecting) this.accumulator.fail("analysis_error");
            }
        }, 125);
    }

    #disable(reason, message) {
        this.disabledReason = reason;
        this.isReady = false;
        this.collecting = false;
        this.samplingEnabled = false;
        this.accumulator.fail(reason);
        this.status.textContent = message;
        if (this.timer) window.clearInterval(this.timer);
        if (this.setupWatchdog) window.clearTimeout(this.setupWatchdog);
        if (typeof this.cancelInitialization === "function") {
            this.cancelInitialization();
        }
        this.cancelInitialization = null;
        if (this.worker) this.worker.terminate();
        this.timer = null;
        this.setupWatchdog = null;
        this.worker = null;
    }
}
