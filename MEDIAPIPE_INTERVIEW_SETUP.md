# MediaPipe Interview Camera Setup

CareerGPS camera coaching runs in the candidate's browser. It sends only compact per-answer measurements to Django. It does not upload or store camera video, image frames, segmentation masks, or pose/face landmark streams.

## Repository and frontend architecture

CareerGPS is a server-rendered Django application. Interview pages use Django templates and app-owned static JavaScript. The repository has no Node runtime, package manifest, frontend bundler, or Content Security Policy configuration. Phase 4 therefore uses pinned same-origin static assets and native ES modules instead of adding a project-wide build system.

## Pinned assets

The integration uses:

- `@mediapipe/tasks-vision` 1.0.1, self-hosted as an ES module and WebAssembly runtime.
- Pose Landmarker Lite float16 model, model revision 1.
- Face Landmarker float16 model, model revision 1.

Face Landmarker supplies the required face coverage, framing, and approximate geometric head orientation. Pose Landmarker supplies optional shoulder, hip, and wrist geometry. Blendshapes are disabled. CareerGPS does not use Hand Landmarker, emotion classification, identity recognition, eye-contact labels, or psychological inference.

The committed assets total 45,135,921 bytes: 35,599,579 bytes for the ES module and WebAssembly runtime variants, 5,777,746 bytes for Pose Landmarker Lite, and 3,758,596 bytes for Face Landmarker. Browsers load only the runtime variant selected by MediaPipe plus the two models.

Pinned SHA-256 values are `ee318eaa…aca505f` for the npm runtime archive, `59929e1d…90d574a` for Pose Landmarker Lite, and `64184e22…e0bc9ff` for Face Landmarker. The setup command contains the complete values.

## Reproduce the asset setup

From the project root, run:

```text
python manage.py prepare_mediapipe_assets
```

The command downloads exact versioned URLs and verifies SHA-256 checksums before replacing the app-owned static asset directory. A mismatch stops setup. No Node installation or project-wide bundler is required.

For deployment, run the project's normal `collectstatic` step after preparing assets. Serve `.mjs`, `.js`, `.wasm`, and `.task` files from the same origin under `/static/`. Configure the static server to return JavaScript MIME types for `.mjs` and `application/wasm` for `.wasm`.

Asset preparation requires network access. Once these files are present and served, inference is local and makes no MediaPipe or cloud inference request. Normal CareerGPS audio upload, transcription, and Gemini answer evaluation still follow their existing paths.

## Runtime architecture

The live interview page creates a classic worker at a bounded 8 frames per second. The pinned Emscripten loader uses `importScripts()` to expose its factory in worker scope, while the application controller and metric engine remain ES modules. The worker owns both MediaPipe tasks and performs synchronous `detectForVideo` calls away from the main UI thread. It returns only the few ephemeral points required for deterministic geometry. The main module calibrates a neutral baseline during camera setup, collects only in the Phase 3 `ANSWERING` interval, and immediately aggregates and discards observations at `ANSWER_COMPLETE`.

Camera access requires a secure browser context: HTTPS in deployment or localhost during development. The browser must support `navigator.mediaDevices.getUserMedia`, module workers, `createImageBitmap`, WebAssembly, and ES modules. Current desktop Chrome and Edge are the primary manual-test targets. Unsupported browsers use the explicit audio-only fallback.

## Measurements and thresholds

Setup retains up to 60 valid head calibration observations and requires three consecutive processed webcam frames with usable face landmarks inside the preview. Shoulders, hips, and wrists are never required for readiness. Missing head coverage produces specific repositioning guidance while Retry Camera Analysis and Continue Audio Only remain available. Head and optional movement measurements are normalized by visible face width or expressed as fractions.

- Head orientation uses face geometry relative to the setup baseline. Approximate yaw above 24 degrees, pitch above 22 degrees, or roll above 20 degrees for three frames starts a sustained orientation-change event. It is not a gaze or eye-contact measurement.
- Head-position stability compares successive face centres; relative movement above 0.25 records a large change. Framing checks the face landmarks against conservative preview margins.
- Shoulder analysis uses only visible, valid shoulder pairs. Tilt is the acute angle to the horizontal, always from 0 to 90 degrees even if left/right labels appear reversed. Shoulder stability compares tilt with the optional setup baseline; large shoulder shifts are measured relative to the head.
- Hip analysis uses valid hip pairs and measures torso-centre movement relative to the head. It needs no shoulder landmarks.
- Gesture analysis uses only visible wrists. A normalized successive-frame range above 0.16 is a broad movement event. Cropped wrists reduce coverage instead of becoming zero movement.

Each optional signal is available after at least three valid frames and at least 35% of sampled frames; otherwise its metric group is omitted and its coverage is reported as low or unavailable. Head-only framing is a valid complete answer when head coverage reaches at least three frames and 65% of sampled frames. Other answers with valid head samples are partial; answers without usable head samples are unavailable. Server-side quality is high at 15 seconds, 40 samples, and 75% head coverage; medium at 5 seconds, 12 samples, and 45% head coverage; otherwise it is low. Low quality produces limited coaching rather than treating missing observations as behaviour.

New answers use the `careergps-visual-v2` payload. The server still accepts and renders historical `careergps-visual-v1` rows. Both versions use existing JSON and version fields, so no database migration is needed.

All thresholds are deterministic constants in `interview/static/interview/js/visual_metrics.js`. Server validation limits payload size, keys, versions, numeric ranges, counts, and duration alignment before any audio file is saved.

## Privacy and retention

Camera analysis requires a separate explicit consent choice. Denial, missing hardware, model failure, a disconnected camera, or browser incompatibility never blocks the audio interview. Saved rows contain status, coverage, compact metrics, and deterministic coaching text. Existing interview deletion cascades to the question rows containing those metrics.

Interview rows persist the camera choice, consent flag, and visual schema version. Question rows persist the visual status, compact aggregate object, and a bounded failure reason. Raw frames, video, image blobs, segmentation output, full landmarks, face blendshapes, device identifiers, and browser-authored coaching text are not persisted. Visual data is never included in the Gemini evaluation request and has no employer-facing route.

## Manual webcam verification

Use a current desktop Chrome or Edge browser over HTTPS or localhost:

1. Confirm audio-only spoken and written interviews never request camera permission.
2. Select camera analysis, decline its consent, and confirm creation is blocked with a clear message.
3. Consent, complete the microphone check, allow camera permission, and confirm preview plus model and face-framing readiness with only the head visible.
4. Start a spoken answer and confirm the preview stays responsive while Visual Interview Coaching reports collection.
5. Confirm analysis starts only after `ANSWERING` and stops immediately at `ANSWER_COMPLETE`.
6. Confirm question playback and between-question time are not collected and no coaching appears between questions.
7. Deny permission and confirm “Continue with audio only” completes the interview.
8. Disconnect the camera during an answer and confirm audio continues and the answer is marked partial or unavailable.
9. Move partly outside the frame and verify feedback describes limited framing without psychological labels.
10. Inspect browser network traffic and confirm no image, video, blob, landmark stream, or MediaPipe inference request leaves the browser; only the normal audio upload and compact `visualMetrics` JSON are submitted.
11. Review final feedback and confirm Answer Analysis, Voice Analysis, and Visual Interview Coaching remain separate. Widen the framing and check that shoulder, torso, and hand sections appear only when each signal has enough coverage.
12. Open the feedback as another user and confirm access is denied.
