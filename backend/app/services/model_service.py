"""Model inference service using Hugging Face Transformers with Swin Transformer."""

import logging
import time
from typing import Any, Dict, List, Optional
from PIL import Image

from app.core.config import settings
from app.schemas.detection import ClassScore, InterpretationResult, PredictionResult
from app.schemas.errors import InferenceException, ModelNotReadyException

logger = logging.getLogger(__name__)


STANDARD_LIMITATIONS: List[str] = [
    "This is an AI-generated-image screening result, not proof that an image is authentic, manipulated, or harmful.",
    "Model was trained on 2022 image distributions; newer generative models, edited photos, camera photos, or screenshots may yield inaccurate classifications.",
    "This screening does not establish personal identity, consent, intent, or whether content is intimate or abusive.",
    "Do not use this automated score as the sole basis for legal, protective, or moderation decisions.",
]


def generate_interpretation(label: str, score: float, all_scores: List[ClassScore]) -> InterpretationResult:
    """Generate carefully worded, non-definitive interpretation from model predictions."""
    score_pct = score * 100.0

    # Check for ambiguous score distribution (e.g. difference <= 10%)
    if len(all_scores) >= 2:
        diff = abs(all_scores[0].score - all_scores[1].score)
        if diff < 0.10:
            summary = (
                f"The screening model produced ambiguous scores between classes (leading: '{label}' at {score_pct:.1f}%). "
                "Because class separation is low, this result cannot be reliably interpreted."
            )
            return InterpretationResult(
                category="ai_image_screening",
                summary=summary,
                is_definitive=False,
                limitations=STANDARD_LIMITATIONS,
            )

    if label.lower() == "artificial":
        summary = (
            f"The screening model identified visual characteristics frequently associated with AI-generated imagery "
            f"(score: {score_pct:.1f}%). This is a probabilistic screening indicator, not definitive proof of synthetic origin."
        )
    elif label.lower() == "human":
        summary = (
            f"The screening model identified visual characteristics consistent with human-created or photographic imagery "
            f"(score: {score_pct:.1f}%). This is an initial screening indicator, not definitive proof of authenticity."
        )
    else:
        summary = (
            f"The model predicted class '{label}' with a confidence score of {score_pct:.1f}%. "
            "Please review the image and surrounding context."
        )

    return InterpretationResult(
        category="ai_image_screening",
        summary=summary,
        is_definitive=False,
        limitations=STANDARD_LIMITATIONS,
    )


class ModelService:
    """Service encapsulating Hugging Face model loading and inference."""

    def __init__(self, model_id: Optional[str] = None) -> None:
        self.model_id = model_id or settings.MODEL_ID
        self.model: Any = None
        self.processor: Any = None
        self.device: str = "cpu"
        self.is_loaded: bool = False
        self.labels: List[str] = ["artificial", "human"]
        self.initialization_error: Optional[str] = None

    def load(self) -> bool:
        """Load the model and image processor into memory (CPU by default)."""
        logger.info(f"Initiating model loading for: {self.model_id}")
        start_time = time.time()

        try:
            import torch
            from transformers import AutoImageProcessor, AutoModelForImageClassification

            self.device = "cpu"
            cache_kwargs = {}
            if settings.HF_TOKEN:
                cache_kwargs["token"] = settings.HF_TOKEN
            if settings.HF_HOME:
                cache_kwargs["cache_dir"] = settings.HF_HOME
            elif settings.TRANSFORMERS_CACHE:
                cache_kwargs["cache_dir"] = settings.TRANSFORMERS_CACHE

            # Load processor and model
            self.processor = AutoImageProcessor.from_pretrained(self.model_id, **cache_kwargs)
            self.model = AutoModelForImageClassification.from_pretrained(self.model_id, **cache_kwargs)

            self.model.to(self.device)
            self.model.eval()

            # Extract actual labels from model configuration
            if hasattr(self.model.config, "id2label") and self.model.config.id2label:
                self.labels = [self.model.config.id2label[i] for i in sorted(self.model.config.id2label.keys())]

            self.is_loaded = True
            self.initialization_error = None
            elapsed = time.time() - start_time
            logger.info(f"Model {self.model_id} loaded successfully on {self.device} in {elapsed:.2f}s. Labels: {self.labels}")
            return True

        except Exception as exc:
            self.is_loaded = False
            self.initialization_error = f"{type(exc).__name__}: {str(exc)}"
            logger.warning(
                f"Model initialization for '{self.model_id}' failed: {type(exc).__name__}. "
                "Inference endpoint will return 503 until model is available."
            )
            return False

    def predict(self, image: Image.Image) -> Dict[str, Any]:
        """Perform inference on a validated PIL RGB image via Hugging Face API or local fallback."""
        # 1. Attempt lightweight Hugging Face API inference first if token is configured
        if settings.HF_TOKEN:
            try:
                import io
                import httpx
                buf = io.BytesIO()
                image.save(buf, format="JPEG", quality=90)
                image_bytes = buf.getvalue()

                headers = {"Authorization": f"Bearer {settings.HF_TOKEN}"}
                api_url = getattr(settings, "HF_API_URL", f"https://api-inference.huggingface.co/models/{self.model_id}")
                with httpx.Client(timeout=15.0) as client:
                    resp = client.post(api_url, headers=headers, content=image_bytes)

                if resp.status_code == 200:
                    hf_results = resp.json()
                    if isinstance(hf_results, list) and len(hf_results) > 0 and "label" in hf_results[0]:
                        scores = [
                            ClassScore(label=item["label"], score=round(float(item["score"]), 6))
                            for item in hf_results if "label" in item and "score" in item
                        ]
                        scores.sort(key=lambda s: s.score, reverse=True)
                        top_prediction = scores[0]
                        prediction_result = PredictionResult(
                            label=top_prediction.label,
                            score=top_prediction.score,
                            scores=scores,
                        )
                        interpretation_result = generate_interpretation(
                            label=top_prediction.label,
                            score=top_prediction.score,
                            all_scores=scores,
                        )
                        return {
                            "prediction": prediction_result,
                            "interpretation": interpretation_result,
                        }
            except Exception as hf_err:
                logger.debug(f"HF API inference bypassed, falling back to local model: {hf_err}")

        # 2. Local Model Inference Fallback (On-Demand Lazy Load if not already in memory)
        if not self.is_loaded or self.model is None or self.processor is None:
            logger.info("On-demand local model loading initiated for predict request.")
            success = self.load()
            if not success or self.model is None or self.processor is None:
                raise ModelNotReadyException(
                    message="Model service is currently unavailable or initializing. Please retry shortly.",
                    details={"initialization_error": self.initialization_error},
                )

        try:
            import torch

            # Preprocess image
            inputs = self.processor(images=image, return_tensors="pt")
            inputs = {k: v.to(self.device) for k, v in inputs.items()}

            with torch.no_grad():
                outputs = self.model(**inputs)
                logits = outputs.logits
                probabilities = torch.nn.functional.softmax(logits, dim=-1)[0]

            id2label = getattr(self.model.config, "id2label", None) or {
                0: "artificial",
                1: "human",
            }

            scores: List[ClassScore] = []
            for idx in range(len(probabilities)):
                raw_label = id2label.get(idx, id2label.get(str(idx), f"class_{idx}"))
                prob = float(probabilities[idx].item())
                scores.append(ClassScore(label=raw_label, score=round(prob, 6)))

            # Sort descending by score
            scores.sort(key=lambda s: s.score, reverse=True)
            top_prediction = scores[0]

            prediction_result = PredictionResult(
                label=top_prediction.label,
                score=top_prediction.score,
                scores=scores,
            )

            interpretation_result = generate_interpretation(
                label=top_prediction.label,
                score=top_prediction.score,
                all_scores=scores,
            )

            return {
                "prediction": prediction_result,
                "interpretation": interpretation_result,
            }

        except ModelNotReadyException:
            raise
        except Exception as exc:
            logger.error(f"Inference execution failed: {type(exc).__name__}")
            raise InferenceException(
                message="An error occurred while generating image screening predictions.",
            )


# Global singleton instance for the app lifespan
model_service = ModelService()


def get_model_service() -> ModelService:
    """Dependency provider for FastAPI route injection."""
    return model_service
