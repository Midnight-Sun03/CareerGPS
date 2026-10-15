# CareerGPS spoken interview setup

Phase 3 uses `faster-whisper==1.2.1` with its integrated Silero voice activity
detection. The default model is `base.en`, running on CPU with int8
quantisation. This is intended for the current local/development architecture.

## Install the runtime

Install the pinned project requirements in the normal virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Prepare the model before serving requests

CareerGPS defaults to `INTERVIEW_WHISPER_LOCAL_FILES_ONLY=true`. A request will
therefore never download model files silently. Pre-cache `base.en` during setup,
or download a CTranslate2 faster-whisper model into a managed directory and set:

```text
INTERVIEW_WHISPER_MODEL_PATH=C:\path\to\faster-whisper-base-en
```

If a deployment deliberately permits a one-time cache download during a setup
step, run this outside the web request lifecycle:

```powershell
.\.venv\Scripts\python.exe -c "from faster_whisper import WhisperModel; WhisperModel('base.en', device='cpu', compute_type='int8')"
```

Then keep `INTERVIEW_WHISPER_LOCAL_FILES_ONLY=true` for the application.

Available settings:

- `INTERVIEW_WHISPER_MODEL` defaults to `base.en`.
- `INTERVIEW_WHISPER_MODEL_PATH` overrides the model name with a local folder.
- `INTERVIEW_WHISPER_DEVICE` defaults to `cpu`.
- `INTERVIEW_WHISPER_COMPUTE_TYPE` defaults to `int8`.
- `INTERVIEW_WHISPER_LOCAL_FILES_ONLY` defaults to `true`.

The model is loaded once per application process and reused. If the package,
model, decoder or VAD cannot run, raw audio remains saved and the transcript or
affected measurements are marked unavailable. The candidate can enter a manual
transcript for answer-content evaluation; unavailable voice evidence stays
unavailable.

The meaningful-pause threshold is defined once as
`MIN_MEANINGFUL_PAUSE_SECONDS` in `interview/services/delivery_metrics.py` and
is currently 1.0 second.
