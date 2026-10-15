let poseLandmarker = null;
let faceLandmarker = null;

function compactPoint(point) {
    if (!point) return null;
    return {x: point.x, y: point.y, z: point.z, visibility: point.visibility ?? 1};
}

self.onmessage = async event => {
    const message = event.data;
    if (message.type === "init") {
        try {
            const vision = await import(message.bundleUrl);
            const fileset = await vision.FilesetResolver.forVisionTasks(message.wasmRootUrl);
            self.postMessage({type: "progress", stage: "runtime_ready"});
            poseLandmarker = await vision.PoseLandmarker.createFromOptions(fileset, {
                baseOptions: {modelAssetPath: message.poseModelUrl},
                runningMode: "VIDEO",
                numPoses: 1,
                minPoseDetectionConfidence: 0.5,
                minPosePresenceConfidence: 0.5,
                minTrackingConfidence: 0.5,
                outputSegmentationMasks: false,
            });
            self.postMessage({type: "progress", stage: "pose_ready"});
            faceLandmarker = await vision.FaceLandmarker.createFromOptions(fileset, {
                baseOptions: {modelAssetPath: message.faceModelUrl},
                runningMode: "VIDEO",
                numFaces: 1,
                minFaceDetectionConfidence: 0.5,
                minFacePresenceConfidence: 0.5,
                minTrackingConfidence: 0.5,
                outputFaceBlendshapes: false,
                outputFacialTransformationMatrixes: false,
            });
            self.postMessage({type: "progress", stage: "face_ready"});
            self.postMessage({type: "ready"});
        } catch (error) {
            self.postMessage({type: "error", reason: "model_load_failed", detail: String(error)});
        }
        return;
    }
    if (message.type !== "frame" || !poseLandmarker || !faceLandmarker) return;
    const started = performance.now();
    try {
        const poseResult = poseLandmarker.detectForVideo(message.bitmap, message.timestamp);
        const faceResult = faceLandmarker.detectForVideo(message.bitmap, message.timestamp);
        const pose = poseResult.landmarks?.[0];
        const face = faceResult.faceLandmarks?.[0];
        self.postMessage({
            type: "observation",
            inferenceMs: performance.now() - started,
            pose: pose ? {
                nose: compactPoint(pose[0]),
                leftShoulder: compactPoint(pose[11]),
                rightShoulder: compactPoint(pose[12]),
                leftWrist: compactPoint(pose[15]),
                rightWrist: compactPoint(pose[16]),
                leftHip: compactPoint(pose[23]),
                rightHip: compactPoint(pose[24]),
            } : null,
            face: face ? {
                nose: compactPoint(face[1]),
                leftEye: compactPoint(face[33]),
                rightEye: compactPoint(face[263]),
                forehead: compactPoint(face[10]),
                chin: compactPoint(face[152]),
            } : null,
        });
    } catch (error) {
        self.postMessage({type: "error", reason: "analysis_error", detail: String(error)});
    } finally {
        message.bitmap.close();
        self.postMessage({type: "frame_complete"});
    }
};
