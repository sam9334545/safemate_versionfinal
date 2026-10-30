import bcrypt
if not hasattr(bcrypt, "__about__"):
    class _About:
        __version__ = getattr(bcrypt, "__version__", "4.0.1")
    bcrypt.__about__ = _About()
_orig_hashpw = bcrypt.hashpw
bcrypt.hashpw = lambda p, s: _orig_hashpw(p[:72] if isinstance(p, (bytes, bytearray)) else p, s)
"""Pytest fixtures and mocked dependencies for isolated, fast test execution."""

import io
import os
import tempfile
from typing import Any, Dict, Generator, Optional
import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app
from app.schemas.deepfake import STANDARD_VIDEO_LIMITATIONS
from app.schemas.detection import ClassScore, InterpretationResult, PredictionResult
from app.schemas.errors import InferenceException, ModelNotReadyException, ValidationException
from app.services.model_service import ModelService, get_model_service
from app.services.video_deepfake_service import VideoDeepfakeService, get_video_deepfake_service


class FakeModelService(ModelService):
    """Deterministic mock model service for unit testing without downloading HF weights."""

    def __init__(
        self,
        is_loaded: bool = True,
        fail_inference: bool = False,
        artificial_score: float = 0.9421,
        human_score: float = 0.0579,
    ) -> None:
        super().__init__(model_id="umm-maybe/AI-image-detector")
        self.is_loaded = is_loaded
        self.fail_inference = fail_inference
        self.device = "cpu"
        self.labels = ["artificial", "human"]
        self.artificial_score = artificial_score
        self.human_score = human_score

    def load(self) -> bool:
        return self.is_loaded

    def predict(self, image: Image.Image):
        if not self.is_loaded:
            raise ModelNotReadyException("Model service is currently unavailable.")
        if self.fail_inference:
            raise InferenceException("Mocked inference failure occurred.")

        scores = [
            ClassScore(label="artificial", score=self.artificial_score),
            ClassScore(label="human", score=self.human_score),
        ]
        scores.sort(key=lambda s: s.score, reverse=True)
        top = scores[0]

        prediction_result = PredictionResult(
            label=top.label,
            score=top.score,
            scores=scores,
        )

        interpretation_result = InterpretationResult(
            category="ai_image_screening",
            summary=f"The screening model identified visual characteristics frequently associated with AI-generated imagery (score: {top.score * 100:.1f}%).",
            is_definitive=False,
            limitations=[
                "This is an AI-generated-image screening result, not proof that an image is authentic, manipulated, or harmful.",
                "Model was trained on 2022 image distributions; newer generative models, edited photos, camera photos, or screenshots may yield inaccurate classifications.",
                "This screening does not establish personal identity, consent, intent, or whether content is intimate or abusive.",
                "Do not use this automated score as the sole basis for legal, protective, or moderation decisions.",
            ],
        )

        return {
            "prediction": prediction_result,
            "interpretation": interpretation_result,
        }


class FakeVideoDeepfakeService(VideoDeepfakeService):
    """Deterministic mock video deepfake service for unit testing."""

    def __init__(
        self,
        is_loaded: bool = True,
        fail_inference: bool = False,
        artificial_score: float = 0.88,
        no_face: bool = False,
    ) -> None:
        super().__init__(model_id="KoreaPeter/ms-eff-gcvit-deepfake-b0-ff-plus-plus")
        self.is_loaded = is_loaded
        self.fail_inference = fail_inference
        self.device = "cpu"
        self.artificial_score = artificial_score
        self.no_face = no_face
        if is_loaded:
            self.model = object()
            self.detector = object()
        else:
            self.model = None
            self.detector = None

    def load(self) -> bool:
        return self.is_loaded

    def predict_video(
        self,
        video_bytes: bytes,
        filename: Optional[str] = None,
        num_frames: Optional[int] = None,
    ) -> Dict[str, Any]:
        if not self.is_loaded:
            raise ModelNotReadyException("Video deepfake screening model is currently unavailable.")
        if self.fail_inference:
            raise InferenceException("Mocked video inference failure occurred.")

        if b"corrupt" in video_bytes:
            raise ValidationException(
                message="The uploaded file could not be decoded as a valid video stream.",
                code="INVALID_VIDEO",
                status_code=400,
            )

        frames_sampled = num_frames or 20
        if self.no_face:
            return {
                "status": "no_face_detected",
                "prediction": None,
                "score": None,
                "score_type": "confidence_score",
                "model_name": self.model_id,
                "frames_sampled": frames_sampled,
                "faces_detected": 0,
                "message": "No human face was detected in the sampled video frames. Video deepfake screening requires visible facial features.",
                "limitations": STANDARD_VIDEO_LIMITATIONS,
            }

        score = self.artificial_score
        if score >= 0.70:
            pred = "potential_manipulation_detected"
            msg = f"Automated screening detected visual patterns frequently associated with digital face manipulation (confidence score: {score * 100:.1f}%)."
        elif score <= 0.35:
            pred = "no_manipulation_detected_by_model"
            msg = f"Automated screening found minimal evidence of manipulation artifacts (confidence score: {score * 100:.1f}%)."
        else:
            pred = "inconclusive"
            msg = f"Automated screening yielded ambiguous cues (confidence score: {score * 100:.1f}%)."

        return {
            "status": "success",
            "prediction": pred,
            "score": score,
            "score_type": "confidence_score",
            "model_name": self.model_id,
            "frames_sampled": frames_sampled,
            "faces_detected": min(frames_sampled, 18),
            "message": msg,
            "limitations": STANDARD_VIDEO_LIMITATIONS,
        }


@pytest.fixture(autouse=True)
def mock_startup_load(monkeypatch):
    """Ensure startup lifespan never downloads models during automated tests."""
    monkeypatch.setattr("app.main.model_service.load", lambda: True)
    monkeypatch.setattr("app.services.model_service.model_service.load", lambda: True)
    monkeypatch.setattr("app.main.video_deepfake_service.load", lambda: True)
    monkeypatch.setattr("app.services.video_deepfake_service.video_deepfake_service.load", lambda: True)


@pytest.fixture
def fake_model_service() -> FakeModelService:
    """Provide a default ready fake image model service."""
    return FakeModelService(is_loaded=True)


@pytest.fixture
def fake_video_service() -> FakeVideoDeepfakeService:
    """Provide a default ready fake video deepfake service."""
    return FakeVideoDeepfakeService(is_loaded=True)


@pytest.fixture
def app_with_mock(fake_model_service: FakeModelService, fake_video_service: FakeVideoDeepfakeService):
    """FastAPI application with injected fake model services."""
    app = create_app()
    app.dependency_overrides[get_model_service] = lambda: fake_model_service
    app.dependency_overrides[get_video_deepfake_service] = lambda: fake_video_service
    return app


@pytest.fixture
def client(app_with_mock) -> Generator[TestClient, None, None]:
    """TestClient configured with dependency overrides."""
    with TestClient(app_with_mock) as test_client:
        yield test_client


@pytest.fixture
def valid_jpeg_bytes() -> bytes:
    """Generate a valid small RGB JPEG in memory."""
    img = Image.new("RGB", (120, 120), color=(180, 50, 70))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def valid_png_bytes() -> bytes:
    """Generate a valid small RGB PNG in memory."""
    img = Image.new("RGB", (100, 100), color=(50, 120, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def valid_webp_bytes() -> bytes:
    """Generate a valid small RGB WEBP in memory."""
    img = Image.new("RGB", (80, 80), color=(80, 180, 80))
    buf = io.BytesIO()
    img.save(buf, format="WEBP")
    return buf.getvalue()


@pytest.fixture
def animated_gif_bytes() -> bytes:
    """Generate an animated multi-frame GIF in memory."""
    frame1 = Image.new("RGB", (50, 50), color="red")
    frame2 = Image.new("RGB", (50, 50), color="blue")
    buf = io.BytesIO()
    frame1.save(
        buf,
        format="GIF",
        save_all=True,
        append_images=[frame2],
        duration=100,
        loop=0,
    )
    return buf.getvalue()


@pytest.fixture
def corrupt_bytes() -> bytes:
    """Corrupt byte sequence pretending to be an image or video."""
    return b"\x00\x00\x00\x20ftypisom" + b"corrupt_garbage_bytes" * 20


@pytest.fixture
def valid_mp4_bytes() -> bytes:
    """Generate a small valid synthetic MP4 video in memory."""
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp_name = tmp.name

    try:
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        out = cv2.VideoWriter(tmp_name, fourcc, 10.0, (64, 64))
        for _ in range(5):
            frame = np.zeros((64, 64, 3), dtype=np.uint8)
            frame[:] = (50, 100, 150)
            out.write(frame)
        out.release()

        with open(tmp_name, "rb") as f:
            data = f.read()
        return data
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
