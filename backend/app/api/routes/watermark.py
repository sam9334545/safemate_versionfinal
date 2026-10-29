"""Forensic Leak Tracer API routes for embedding and extracting invisible watermarks."""

from __future__ import annotations

import json
import logging
from typing import Any, List, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status

from app.models import UserModel
from app.utils.auth import get_current_user

from app.schemas.watermark import WatermarkExtractResponse
from app.services.watermark_service import watermark_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/watermark", tags=["Forensic Leak Tracer"])


def _creator_identity(user: UserModel, session_ts: Optional[str]) -> dict:
    """Build the creator identity from the AUTHENTICATED user (never from client-supplied fields)."""
    import datetime as _dt

    is_guest = bool(getattr(user, "is_guest", False))
    label = f"{user.email} (ID {user.id}{', guest session' if is_guest else ''})"
    return {
        "owner_id": f"usr_{user.id}",
        "creator_label": label,
        "session_ts": (session_ts or "").strip() or _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }


def _parse_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() not in ("false", "0", "no", "off", "none", "")
    return bool(val)


@router.post(
    "/embed",
    summary="Embed Invisible Recipient Watermark",
    description="Embeds a visually imperceptible, cryptographically signed watermark identifying a specific recipient.",
)
async def embed_watermark(
    file: UploadFile = File(..., description="Target image file (JPEG, PNG, WEBP)"),
    recipient_name: str = Form(..., description="Name of the person who will receive this copy"),
    recipient_id: Optional[str] = Form(None, description="Optional unique identifier for recipient"),
    owner_id: Optional[str] = Form(None, description="Ignored: the owner is taken from the authenticated session"),
    session_started_at: Optional[str] = Form(None, description="ISO timestamp when the user's session began"),
    notes: Optional[str] = Form(None, description="Optional sharing terms or notes"),
    visible_stamp: Any = Form(True, description="Whether to burn visible recipient security banner"),
    current_user: UserModel = Depends(get_current_user),
):
    """Embed an invisible watermark into an image and return the protected PNG file (login required)."""
    identity = _creator_identity(current_user, session_started_at)
    image_bytes = await file.read()
    output_bytes, media_type, meta = watermark_service.embed(
        image_bytes=image_bytes,
        recipient_name=recipient_name,
        recipient_id=recipient_id,
        owner_id=identity["owner_id"],
        creator_label=identity["creator_label"],
        session_ts=identity["session_ts"],
        notes=notes,
        visible_stamp=_parse_bool(visible_stamp),
    )

    clean_name = "".join(c for c in recipient_name if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_")
    filename = f"safemate_copy_{clean_name}.png"

    return Response(
        content=output_bytes,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Watermark-Recipient": recipient_name,
            "X-Watermark-ID": meta["recipient_id"],
            "X-Watermark-Fingerprint": meta["fingerprint"],
            "X-Watermark-Creator": identity["creator_label"].encode("ascii", "ignore").decode(),
        },
    )


@router.post(
    "/batch-embed",
    summary="Batch Generate Watermarked Copies for Multiple Recipients",
    description="Generates a downloadable ZIP containing individually watermarked copies for each recipient plus an audit manifest.",
)
async def batch_embed_watermarks(
    file: UploadFile = File(..., description="Image to share with multiple people"),
    recipients: str = Form(..., description="Comma-separated or JSON list of recipient names (e.g. 'Liam, Jake, Sarah')"),
    owner_id: Optional[str] = Form(None, description="Ignored: the owner is taken from the authenticated session"),
    session_started_at: Optional[str] = Form(None, description="ISO timestamp when the user's session began"),
    notes: Optional[str] = Form(None, description="Optional sharing context"),
    visible_stamp: Any = Form(True, description="Whether to burn visible recipient security banner"),
    current_user: UserModel = Depends(get_current_user),
):
    """Batch embed watermarks for multiple recipients and return a ZIP file."""
    # Parse comma-separated or JSON array of names
    recipient_list: List[str] = []
    recipients_trimmed = recipients.strip()
    if recipients_trimmed.startswith("["):
        try:
            parsed = json.loads(recipients_trimmed)
            if isinstance(parsed, list):
                recipient_list = [str(item) for item in parsed]
        except Exception:
            pass

    if not recipient_list:
        recipient_list = [item.strip() for item in recipients.split(",") if item.strip()]

    if not recipient_list:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please provide at least one recipient name.",
        )

    identity = _creator_identity(current_user, session_started_at)
    image_bytes = await file.read()
    zip_bytes, media_type = watermark_service.batch_embed(
        image_bytes=image_bytes,
        recipients=recipient_list,
        owner_id=identity["owner_id"],
        creator_label=identity["creator_label"],
        session_ts=identity["session_ts"],
        notes=notes,
        visible_stamp=_parse_bool(visible_stamp),
    )

    return Response(
        content=zip_bytes,
        media_type=media_type,
        headers={
            "Content-Disposition": 'attachment; filename="safemate_protected_bundle.zip"',
            "X-Recipient-Count": str(len(recipient_list)),
        },
    )


@router.post(
    "/extract",
    response_model=WatermarkExtractResponse,
    summary="Extract Watermark from Suspected Leaked Media",
    description="Decodes hidden forensic watermarks from uploaded image to trace which recipient leaked the content.",
)
async def extract_watermark(
    file: UploadFile = File(..., description="Suspected leaked screenshot or photograph"),
) -> WatermarkExtractResponse:
    """Inspect image and return forensic analysis report identifying the leaker."""
    image_bytes = await file.read()
    return watermark_service.extract(image_bytes)


@router.get("/health")
async def watermark_health():
    """Health status probe for forensic watermark engine."""
    return {
        "status": "healthy",
        "service": "forensic_leak_tracer",
        "algorithm": "spatial_lsb_redundant_hmac_sha256",
    }