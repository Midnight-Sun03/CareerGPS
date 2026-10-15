from __future__ import annotations

import hashlib
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


RUNTIME_URL = "https://registry.npmjs.org/@mediapipe/tasks-vision/-/tasks-vision-1.0.1.tgz"
RUNTIME_SHA256 = "ee318eaa3d42230aa10910d114faf2a488c577c4e4d33c7cb04126924aca505f"
POSE_URL = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task"
POSE_SHA256 = "59929e1d1ee95287735ddd833b19cf4ac46d29bc7afddbbf6753c459690d574a"
FACE_URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
FACE_SHA256 = "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"

RUNTIME_FILES = (
    "vision_bundle.mjs",
    "wasm/vision_wasm_internal.js",
    "wasm/vision_wasm_internal.wasm",
    "wasm/vision_wasm_module_internal.js",
    "wasm/vision_wasm_module_internal.wasm",
    "wasm/vision_wasm_nosimd_internal.js",
    "wasm/vision_wasm_nosimd_internal.wasm",
)


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url, target, expected_hash):
    urllib.request.urlretrieve(url, target)
    actual = _sha256(target)
    if actual != expected_hash:
        raise CommandError(
            f"Checksum mismatch for {url}: expected {expected_hash}, received {actual}."
        )


class Command(BaseCommand):
    help = "Download and verify the pinned browser-local MediaPipe runtime and models."

    def handle(self, *args, **options):
        static_root = Path(settings.BASE_DIR) / "interview" / "static" / "interview"
        runtime_target = static_root / "vendor" / "mediapipe" / "1.0.1"
        model_target = static_root / "models" / "mediapipe"
        with tempfile.TemporaryDirectory(prefix="careergps-mediapipe-") as temp_name:
            temp = Path(temp_name)
            archive = temp / "tasks-vision-1.0.1.tgz"
            pose = temp / "pose_landmarker_lite-v1.task"
            face = temp / "face_landmarker-v1.task"
            self.stdout.write("Downloading pinned MediaPipe runtime…")
            _download(RUNTIME_URL, archive, RUNTIME_SHA256)
            self.stdout.write("Downloading pinned Pose Landmarker Lite model…")
            _download(POSE_URL, pose, POSE_SHA256)
            self.stdout.write("Downloading pinned Face Landmarker model…")
            _download(FACE_URL, face, FACE_SHA256)

            extracted = temp / "runtime"
            with tarfile.open(archive, "r:gz") as package:
                members = {member.name: member for member in package.getmembers()}
                for relative in RUNTIME_FILES:
                    package_name = f"package/{relative}"
                    member = members.get(package_name)
                    if member is None or not member.isfile():
                        raise CommandError(f"Pinned package is missing {package_name}.")
                    source = package.extractfile(member)
                    destination = extracted / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with source, destination.open("wb") as output:
                        shutil.copyfileobj(source, output)

            if runtime_target.exists():
                shutil.rmtree(runtime_target)
            runtime_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(extracted, runtime_target)
            model_target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(pose, model_target / pose.name)
            shutil.copy2(face, model_target / face.name)

        self.stdout.write(self.style.SUCCESS("Pinned MediaPipe assets are ready and checksum-verified."))
