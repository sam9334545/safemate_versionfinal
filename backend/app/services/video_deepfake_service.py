from __future__ import annotations
"""Video deepfake screening service utilizing MS-EffGCViT and YOLO face detection."""

import logging
import os
import tempfile
import time
from typing import Any, Dict, List, Optional
import cv2
import numpy as np

try:
    import torch
    _IMAGENET_MEAN = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
    _IMAGENET_STD = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)
except ImportError:
    torch = None
    _IMAGENET_MEAN = None
    _IMAGENET_STD = None

from app.core.config import settings
from app.schemas.deepfake import STANDARD_VIDEO_LIMITATIONS
from app.schemas.errors import InferenceException, ModelNotReadyException, ValidationException

logger = logging.getLogger(__name__)


class VideoDeepfakeService:
    """Service encapsulating video frame extraction, face detection, and MS-EffGCViT inference."""

    def __init__(self, model_id: Optional[str] = None) -> None:
        self.model_id = model_id or settings.VIDEO_DEEPFAKE_MODEL_ID
        self.model: Any = None
        self.detector: Any = None
        self.device: str = "cpu"
        self.is_loaded: bool = False
        self.initialization_error: Optional[str] = None

    def load(self) -> bool:
        """Initialize the MS-EffGCViT-B0 classifier and YOLO face detector."""
        logger.info(f"Initiating video deepfake model loading for: {self.model_id}")
        start_time = time.time()

        try:
            from deepguard import ms_eff_gcvit_b0
            from huggingface_hub import hf_hub_download
            from safetensors.torch import load_file
            from ultralytics import YOLO

            # 1. Download model weights and face detector weights from HF Hub
            hf_kwargs = {"token": settings.HF_TOKEN} if settings.HF_TOKEN else {}
            model_weights_path = hf_hub_download(self.model_id, "model.safetensors", **hf_kwargs)
            face_detector_path = hf_hub_download(self.model_id, "yolov8n-face.pt", **hf_kwargs)

            # 2. Instantiate and load MS-EffGCViT-B0
            self.model = ms_eff_gcvit_b0(pretrained=False)
            state_dict = load_file(model_weights_path)
            # Remove any wrapping 'model.' prefix if present
            cleaned_state_dict = {
                (k[6:] if k.startswith("model.") else k): v for k, v in state_dict.items()
            }
            self.model.load_state_dict(cleaned_state_dict, strict=False)
            self.model.to(self.device)
            self.model.eval()

            # 3. Instantiate YOLO face detector
            self.detector = YOLO(face_detector_path, task="detect")

            self.is_loaded = True
            self.initialization_error = None
            elapsed = time.time() - start_time
            logger.info(f"Video deepfake model and face detector loaded successfully in {elapsed:.2f}s.")
            return True

        except Exception as exc:
            self.is_loaded = False
            self.initialization_error = f"{type(exc).__name__}: {str(exc)}"
            logger.warning(
                f"Video deepfake model loading failed: {type(exc).__name__}: {exc}. "
                "Video deepfake endpoint will return 503 until model is ready."
            )
            return False

    def _sample_frames(self, video_path: str, num_frames: int) -> List[np.ndarray]:
        """Evenly sample frames from the video file with resolution, duration, and frame bounds."""
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            cap.release()
            raise ValidationException(
                message="The uploaded file could not be decoded as a valid video stream.",
                code="INVALID_VIDEO",
                status_code=400,
            )

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            cap.release()
            raise ValidationException(
                message="Video stream contains 0 readable frames or is corrupted.",
                code="INVALID_VIDEO",
                status_code=400,
            )

        # 1. Enforce resolution limits
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if (
            width > settings.MAX_VIDEO_RESOLUTION_DIMENSION
            or height > settings.MAX_VIDEO_RESOLUTION_DIMENSION
        ):
            cap.release()
            raise ValidationException(
                message=f"Video dimensions ({width}x{height}) exceed maximum allowed dimension of {settings.MAX_VIDEO_RESOLUTION_DIMENSION}px.",
                code="EXCESSIVE_DIMENSIONS",
                status_code=400,
            )

        # 2. Enforce video duration limits
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps and fps > 0:
            duration_sec = total_frames / fps
            if duration_sec > settings.MAX_VIDEO_DURATION_SECONDS:
                cap.release()
                raise ValidationException(
                    message=f"Video duration ({duration_sec:.1f}s) exceeds maximum allowed duration of {settings.MAX_VIDEO_DURATION_SECONDS}s.",
                    code="VIDEO_TOO_LONG",
                    status_code=400,
                )

        indices = np.linspace(0, max(total_frames - 1, 0), num_frames, dtype=int)
        frames = []
        for idx in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
            ret, frame = cap.read()
            if ret and frame is not None:
                # Downscale frames larger than 1080p to conserve memory and CPU processing
                fh, fw = frame.shape[:2]
                if max(fh, fw) > 1080:
                    scale = 1080.0 / max(fh, fw)
                    frame = cv2.resize(
                        frame, (int(fw * scale), int(fh * scale)), interpolation=cv2.INTER_AREA
                    )

                # Convert BGR (OpenCV standard) to RGB
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                frames.append(rgb_frame)

        cap.release()
        return frames

    def _crop_face(
        self,
        img: np.ndarray,
        margin_ratio: float = 0.2,
        conf_thres: float = 0.5,
        min_face_ratio: float = 0.01,
    ) -> Optional[np.ndarray]:
        """Detect and crop the primary face from a single image frame."""
        if self.detector is None:
            return None

        try:
            results = self.detector(img, conf=conf_thres, verbose=False)[0]
            if len(results.boxes) == 0:
                return None

            data = results.boxes.data.cpu().numpy()
            if data.ndim < 2 or data.shape[0] == 0:
                return None

            # Support both class 0 (face in multi-class) and single-class detectors
            if data.shape[1] >= 6:
                faces = data[data[:, 5] == 0]
            else:
                faces = data

            if len(faces) == 0:
                return None

            # Select largest face by bounding box area
            areas = (faces[:, 2] - faces[:, 0]) * (faces[:, 3] - faces[:, 1])
            idx = int(np.argmax(areas))
            h, w = img.shape[:2]
            if areas[idx] / (h * w) < min_face_ratio:
                return None

            xmin, ymin, xmax, ymax = faces[idx, :4]
            pad_w = int((xmax - xmin) * margin_ratio)
            pad_h = int((ymax - ymin) * margin_ratio)
            y1, y2 = max(int(ymin - pad_h), 0), min(int(ymax + pad_h), h)
            x1, x2 = max(int(xmin - pad_w), 0), min(int(xmax + pad_w), w)

            crop = img[y1:y2, x1:x2]
            return crop if crop.size > 0 else None
        except Exception as exc:
            logger.debug(f"Face detection encountered an issue on frame: {exc}")
            return None

    @staticmethod
    def _normalize_face(crop: np.ndarray, target_size: int = 224) -> torch.Tensor:
        """Resize and normalize face crop with ImageNet statistics for MS-EffGCViT."""
        resized = cv2.resize(crop, (target_size, target_size), interpolation=cv2.INTER_LINEAR)
        # Convert HWC uint8 [0, 255] to CHW float32 [0.0, 1.0]
        tensor = torch.from_numpy(resized).permute(2, 0, 1).float() / 255.0
        return (tensor - _IMAGENET_MEAN) / _IMAGENET_STD

    @staticmethod
    def _aggregate_predictions(preds: np.ndarray, agg_mode: str = "conf") -> float:
        """Aggregate per-frame fake probabilities using confidence heuristics."""
        sz = len(preds)
        if sz == 0:
            return 0.0
        if agg_mode == "mean":
            return float(np.mean(preds))
        if agg_mode == "vote":
            return float(np.sum(preds > 0.5) / sz)

        # 'conf' heuristic from DeepGuard
        t_fake, t_real = 0.8, 0.2
        fakes = preds[preds > t_fake]
        if len(fakes) > (sz // 2.5):
            return float(np.mean(fakes))
        if np.count_nonzero(preds < t_real) > 0.9 * sz:
            return float(np.mean(preds[preds < t_real]))
        return float(np.mean(preds))

    def predict_video(
        self,
        video_bytes: bytes,
        filename: Optional[str] = None,
        num_frames: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Process an uploaded video safely in an ephemeral temporary file."""
        if not self.is_loaded or self.model is None:
            raise ModelNotReadyException(
                message="Video deepfake screening model is currently unavailable or initializing.",
            )

        frames_to_sample = num_frames or settings.MAX_VIDEO_FRAMES_SAMPLED
        frames_to_sample = min(max(frames_to_sample, 5), 30)

        # Determine extension
        ext = ".mp4"
        if filename:
            _, raw_ext = os.path.splitext(filename.lower())
            if raw_ext in settings.ALLOWED_VIDEO_EXTENSIONS:
                ext = raw_ext

        temp_path = None
        try:
            # Create a secure temporary file with random suffix
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp_file:
                tmp_file.write(video_bytes)
                temp_path = tmp_file.name

            # 1. Sample frames
            frames = self._sample_frames(temp_path, frames_to_sample)
            if not frames:
                raise ValidationException(
                    message="Could not extract any valid video frames.",
                    code="INVALID_VIDEO",
                    status_code=400,
                )

            # 2. Detect & crop faces
            face_tensors = []
            for frame in frames:
                crop = self._crop_face(frame)
                if crop is not None:
                    face_tensors.append(self._normalize_face(crop))

            # Handle case where video has no detectable faces
            if not face_tensors:
                return {
                    "status": "no_face_detected",
                    "prediction": None,
                    "score": None,
                    "score_type": "confidence_score",
                    "model_name": self.model_id,
                    "frames_sampled": len(frames),
                    "faces_detected": 0,
                    "message": "No human face was detected in the sampled video frames. Video deepfake screening requires visible facial features.",
                    "limitations": STANDARD_VIDEO_LIMITATIONS,
                }

            # 3. Batched forward inference
            batch = torch.stack(face_tensors).to(self.device)
            with torch.no_grad():
                logits = self.model(batch)
                # Apply sigmoid to extract fake probability per frame
                frame_probs = torch.sigmoid(logits).squeeze(-1).cpu().numpy()

            # If scalar array
            if frame_probs.ndim == 0:
                frame_probs = np.array([float(frame_probs)])

            # 4. Aggregate frame probabilities
            final_score = self._aggregate_predictions(frame_probs, agg_mode="conf")
            rounded_score = round(final_score, 4)

            # 5. Formulate neutral classification
            if rounded_score >= 0.70:
                prediction_label = "potential_manipulation_detected"
                message = (
                    f"Automated screening detected visual patterns frequently associated with digital face manipulation "
                    f"(confidence score: {rounded_score * 100:.1f}% across {len(face_tensors)} analyzed face crops). "
                    "This screening score is probabilistic and not definitive proof of manipulation."
                )
            elif rounded_score <= 0.35:
                prediction_label = "no_manipulation_detected_by_model"
                message = (
                    f"Automated screening found minimal evidence of manipulation artifacts "
                    f"(confidence score: {rounded_score * 100:.1f}% across {len(face_tensors)} analyzed face crops). "
                    "This indicates natural consistency under the model's training distribution."
                )
            else:
                prediction_label = "inconclusive"
                message = (
                    f"Automated screening yielded ambiguous cues (confidence score: {rounded_score * 100:.1f}%). "
                    "The video could not be reliably classified as authentic or manipulated."
                )

            return {
                "status": "success",
                "prediction": prediction_label,
                "score": rounded_score,
                "score_type": "confidence_score",
                "model_name": self.model_id,
                "frames_sampled": len(frames),
                "faces_detected": len(face_tensors),
                "message": message,
                "limitations": STANDARD_VIDEO_LIMITATIONS,
            }

        except (ValidationException, ModelNotReadyException):
            raise
        except Exception as exc:
            logger.error(f"Video deepfake inference failure: {type(exc).__name__}: {exc}")
            raise InferenceException(
                message="An error occurred while processing video deepfake screening.",
            )
        finally:
            # Guarantee prompt deletion of temporary video from disk
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception as del_err:
                    logger.warning(f"Failed to remove temporary video file {temp_path}: {del_err}")


# Global singleton instance for the app
video_deepfake_service = VideoDeepfakeService()


def get_video_deepfake_service() -> VideoDeepfakeService:
    """Dependency provider for FastAPI route injection."""
    return video_deepfake_service
