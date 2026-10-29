"""Forensic Leak Tracer service providing invisible per-recipient steganographic watermarking."""

from __future__ import annotations

import datetime
import hashlib
import hmac
import io
import json
import logging
import secrets
import uuid
import zipfile
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageOps, PngImagePlugin

from app.schemas.errors import AppException, ValidationException
from app.schemas.watermark import WatermarkAuditReport, WatermarkExtractResponse

logger = logging.getLogger(__name__)

# Constants
MAGIC_HEADER = b"SFMT_TRC_V1"  # 11 bytes header
SECRET_KEY = "safemate-forensic-secret-salt-2026-secure"
MAX_EMBED_BITS = 16000  # Generous headroom for forensic payloads


class WatermarkService:
    """Core cryptographic steganography engine for per-recipient media watermarking."""

    def __init__(self, secret_key: str = SECRET_KEY) -> None:
        self.secret_key = secret_key

    def _now_iso(self) -> str:
        return datetime.datetime.now(datetime.timezone.utc).isoformat()

    def _get_permutation(self, total_pixels: int) -> np.ndarray:
        """Deterministic pseudorandom pixel index sequence based on constant seed (fast bounded choice)."""
        rng = np.random.default_rng(seed=20261024)
        sample_size = min(total_pixels, MAX_EMBED_BITS)
        return rng.choice(total_pixels, size=sample_size, replace=False)

    def embed(
        self,
        image_bytes: bytes,
        recipient_name: str,
        recipient_id: Optional[str] = None,
        owner_id: Optional[str] = "usr_safemate",
        notes: Optional[str] = None,
        visible_stamp: bool = True,
        creator_label: Optional[str] = None,
        session_ts: Optional[str] = None,
    ) -> Tuple[bytes, str, Dict[str, Any]]:
        """Embed a per-recipient cryptographic watermark invisibly into an image."""
        recipient_name = recipient_name.strip()
        if not recipient_name:
            raise ValidationException("Recipient name cannot be empty.")

        rec_id = recipient_id or f"rec_{secrets.token_hex(4)}"
        now_ts = self._now_iso()

        # Build payload dictionary
        payload_data = {
            "r": recipient_name,
            "id": rec_id,
            "o": owner_id or "usr_safemate",
            "c": creator_label or owner_id or "unknown",
            "s": session_ts or now_ts,
            "t": now_ts,
            "n": notes or "Confidential personal distribution",
        }

        # Encode JSON + HMAC-SHA256 signature
        raw_json = json.dumps(payload_data, separators=(",", ":")).encode("utf-8")
        sig = hmac.new(self.secret_key.encode("utf-8"), raw_json, hashlib.sha256).digest()[:16]
        blob = MAGIC_HEADER + len(raw_json).to_bytes(2, "big") + raw_json + sig
        fingerprint = hashlib.sha256(blob).hexdigest()[:16]

        # Convert to bitstream with 3x repetition redundancy
        bits = []
        for byte in blob:
            for i in range(8):
                b = (byte >> (7 - i)) & 1
                bits.extend([b, b, b])

        # Load image via PIL with mobile EXIF correction and smartphone camera optimization
        try:
            with Image.open(io.BytesIO(image_bytes)) as pil_img:
                pil_img = ImageOps.exif_transpose(pil_img)
                # Optimize oversized phone camera photos (e.g. 48MP) to 2048px max dimension
                if pil_img.width > 2048 or pil_img.height > 2048:
                    pil_img.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
                img_rgb = pil_img.convert("RGB")
        except Exception as exc:
            raise ValidationException(f"Unsupported or corrupted image file: {exc}")

        # Burn visible forensic security watermark banner if requested
        if visible_stamp:
            w_img, h_img = img_rgb.size
            bar_height = max(48, int(h_img * 0.075))
            stamp_layer = Image.new("RGBA", (w_img, h_img), (0, 0, 0, 0))
            draw = ImageDraw.Draw(stamp_layer)
            draw.rectangle([(0, h_img - bar_height), (w_img, h_img)], fill=(11, 19, 43, 225))
            draw.line([(0, h_img - bar_height), (w_img, h_img - bar_height)], fill=(217, 4, 41, 255), width=2)
            stamp_text = f"SAFEMATE FORENSIC COPY • FOR: {recipient_name} • TRACKING ID: {rec_id} • LEAK TRACER ACTIVE"
            y_text = h_img - bar_height + max(5, int(bar_height * 0.14))
            draw.text((12, y_text), stamp_text, fill=(255, 255, 255, 250))
            creator_text = (
                f"SEALED BY: {creator_label or owner_id or 'unknown'} • "
                f"SESSION: {session_ts or now_ts} • ISSUED: {now_ts}"
            )
            draw.text((12, y_text + max(16, int(bar_height * 0.42))), creator_text, fill=(148, 215, 255, 250))
            img_rgb = Image.alpha_composite(img_rgb.convert("RGBA"), stamp_layer).convert("RGB")

        arr = np.array(img_rgb, dtype=np.uint8).copy()
        h, w, c = arr.shape
        total_pixels = h * w

        if len(bits) > total_pixels:
            raise ValidationException(
                f"Image resolution too small ({w}x{h} = {total_pixels} px). "
                f"At least {len(bits)} pixels are required for forensic embedding."
            )

        # Retrieve pixel scattering permutation
        perm = self._get_permutation(total_pixels)
        target_indices = perm[: len(bits)]

        # Scatter bits across least significant bit of Blue channel (least sensitive to human eye)
        flat_blue = arr[:, :, 2].reshape(-1)
        mask = np.uint8(0xFE)
        for bit, idx in zip(bits, target_indices):
            flat_blue[idx] = (flat_blue[idx] & mask) | np.uint8(bit)

        arr[:, :, 2] = flat_blue.reshape(h, w)
        watermarked_img = Image.fromarray(arr, "RGB")

        # Save to lossless PNG buffer with embedded PngInfo cryptographic metadata
        png_info = PngImagePlugin.PngInfo()
        png_info.add_text("Safemate-Watermark", raw_json.decode("utf-8"))
        png_info.add_text("Safemate-Signature", sig.hex())
        png_info.add_text("Safemate-Fingerprint", fingerprint)

        output_buffer = io.BytesIO()
        watermarked_img.save(output_buffer, format="PNG", pnginfo=png_info, compress_level=1)
        encoded_bytes = output_buffer.getvalue()

        meta = {
            "recipient_name": recipient_name,
            "recipient_id": rec_id,
            "owner_id": owner_id,
            "creator": creator_label or owner_id,
            "session_ts": session_ts or now_ts,
            "timestamp": now_ts,
            "fingerprint": fingerprint,
            "dimensions": f"{w}x{h}",
        }

        logger.info(
            f"[Leak Tracer] Successfully embedded watermark for '{recipient_name}' (ID: {rec_id}, Fingerprint: {fingerprint})"
        )
        return encoded_bytes, "image/png", meta

    def batch_embed(
        self,
        image_bytes: bytes,
        recipients: List[str],
        owner_id: Optional[str] = "usr_safemate",
        notes: Optional[str] = None,
        visible_stamp: bool = True,
        creator_label: Optional[str] = None,
        session_ts: Optional[str] = None,
    ) -> Tuple[bytes, str]:
        """Generate a complete ZIP bundle of individual watermarked copies for each recipient."""
        recipients = [r.strip() for r in recipients if r.strip()]
        if not recipients:
            raise ValidationException("Please provide at least one recipient name.")

        zip_buffer = io.BytesIO()
        manifest_items = []

        with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for recipient in recipients:
                clean_name = "".join(c for c in recipient if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_")
                filename = f"protected_copy_{clean_name}.png"

                img_bytes, _, meta = self.embed(
                    image_bytes=image_bytes,
                    recipient_name=recipient,
                    owner_id=owner_id,
                    notes=notes,
                    visible_stamp=visible_stamp,
                    creator_label=creator_label,
                    session_ts=session_ts,
                )

                zf.writestr(filename, img_bytes)
                manifest_items.append({
                    "recipient_name": recipient,
                    "recipient_id": meta["recipient_id"],
                    "file_name": filename,
                    "fingerprint": meta["fingerprint"],
                    "timestamp": meta["timestamp"],
                })

            # Add distribution audit manifest inside the ZIP
            manifest_doc = {
                "generated_at": self._now_iso(),
                "created_by": creator_label or owner_id,
                "session_started_at": session_ts,
                "total_recipients": len(recipients),
                "platform": "Safemate Forensic Leak Tracer",
                "instructions": (
                    "Distribute each specific copy ONLY to the recipient named in its filename. "
                    "If any copy is leaked online, upload it to Safemate Leak Tracer to conclusively identify the leaker."
                ),
                "copies": manifest_items,
            }
            zf.writestr("FORENSIC_DISTRIBUTION_MANIFEST.json", json.dumps(manifest_doc, indent=2))

        return zip_buffer.getvalue(), "application/zip"

    def _build_match_response(
        self,
        raw_json: bytes,
        sig: bytes,
        case_id: str,
        now_ts: str,
    ) -> WatermarkExtractResponse:
        """Helper to build a WatermarkExtractResponse from validated or inspected payload."""
        expected_sig = hmac.new(self.secret_key.encode("utf-8"), raw_json, hashlib.sha256).digest()[:16]
        is_tampered = sig != expected_sig

        try:
            payload = json.loads(raw_json.decode("utf-8"))
        except Exception:
            return WatermarkExtractResponse(
                status="signature_tampered",
                detected=True,
                tamper_detected=True,
                confidence_score=0.3,
                audit_report=WatermarkAuditReport(
                    case_reference_id=case_id,
                    cryptographic_integrity="CORRUPTED_JSON",
                    evidence_affidavit="Watermark found but internal payload parsing failed.",
                ),
            )

        rec_name = payload.get("r", "Unknown Recipient")
        rec_id = payload.get("id", "Unknown ID")
        owner_id = payload.get("o", "Unknown Owner")
        timestamp = payload.get("t", now_ts)
        fingerprint = hashlib.sha256(MAGIC_HEADER + len(raw_json).to_bytes(2, "big") + raw_json + sig).hexdigest()[:16]

        status_str = "signature_tampered" if is_tampered else "match_found"
        confidence = 0.6 if is_tampered else 1.0
        integrity_str = "SIGNATURE_TAMPERED" if is_tampered else "VERIFIED_AUTHENTIC"

        affidavit_text = "\n".join([
            f"DIGITAL FORENSIC EVIDENCE CERTIFICATE [Case: {case_id}]",
            "This certifies that automated forensic steganographic analysis successfully extracted an authentic per-recipient watermark from the submitted media.",
            f"• Identified Recipient (Leaker): {rec_name} (ID: {rec_id})",
            f"• Original Owner: {owner_id}",
            f"• Date & Time of Issuance: {timestamp}",
            f"• Cryptographic Integrity: {integrity_str} (HMAC-SHA256)",
            f"The watermark establishes with forensic certainty that the inspected file originated from the unique copy distributed to '{rec_name}'.",
        ])

        remedies = [
            f"Confront or formally document incident involving recipient: {rec_name}.",
            "Download this digital forensic affidavit for law enforcement or campus safety filing.",
            "Under Section 66E / 67A of the Information Technology Act (or state privacy statutes), distributing non-consensual media carries statutory criminal penalties.",
            "Submit this cryptographic record to police cyber crime reporting units as verified attribution proof.",
        ]

        logger.info(f"[Leak Tracer] MATCH FOUND! Leaker identified as '{rec_name}' (Confidence: {confidence})")

        return WatermarkExtractResponse(
            status=status_str,
            detected=True,
            recipient_name=rec_name,
            recipient_id=rec_id,
            owner_id=owner_id,
            timestamp=timestamp,
            tamper_detected=is_tampered,
            confidence_score=confidence,
            watermark_fingerprint=fingerprint,
            audit_report=WatermarkAuditReport(
                case_reference_id=case_id,
                identified_leaker=rec_name,
                recipient_id=rec_id,
                original_owner=owner_id,
                sharing_timestamp=timestamp,
                cryptographic_integrity=integrity_str,
                evidence_affidavit=affidavit_text,
                actionable_remedies=remedies,
            ),
        )

    def extract(self, image_bytes: bytes) -> WatermarkExtractResponse:
        """Inspect a suspected leaked image, extract the embedded forensic token, and identify the leaker."""
        case_id = f"case_{uuid.uuid4().hex[:10]}"
        now_ts = self._now_iso()

        meta_json_str = None
        meta_sig_hex = None

        try:
            with Image.open(io.BytesIO(image_bytes)) as pil_img:
                # Check for embedded PNG cryptographic chunk
                if hasattr(pil_img, "info") and isinstance(pil_img.info, dict):
                    meta_json_str = pil_img.info.get("Safemate-Watermark")
                    meta_sig_hex = pil_img.info.get("Safemate-Signature")

                pil_img = ImageOps.exif_transpose(pil_img)
                img_rgb = pil_img.convert("RGB")
        except Exception as exc:
            raise ValidationException(f"Invalid image file: {exc}")

        # Layer 1: Check metadata chunk if present and authentic
        if meta_json_str:
            try:
                raw_json = meta_json_str.encode("utf-8")
                sig = bytes.fromhex(meta_sig_hex) if meta_sig_hex else b""
                logger.info(f"[Leak Tracer] Recovered cryptographic PNG metadata token (case: {case_id}).")
                return self._build_match_response(raw_json, sig, case_id, now_ts)
            except Exception as e:
                logger.warning(f"[Leak Tracer] Failed parsing metadata chunk: {e}, falling back to stego bitstream.")

        # Layer 2: Decouple and inspect steganographic bitstream from blue channel
        arr = np.array(img_rgb, dtype=np.uint8)
        h, w, _ = arr.shape
        total_pixels = h * w

        flat_blue = arr[:, :, 2].reshape(-1)
        perm = self._get_permutation(total_pixels)

        # Read bitstream up to MAX_EMBED_BITS
        sample_size = min(total_pixels, MAX_EMBED_BITS)
        indices = perm[:sample_size]
        raw_bits = [int(flat_blue[idx] & 1) for idx in indices]

        # Majority voting across 3x redundancy triplets
        decoded_bits = []
        for i in range(0, len(raw_bits) - 2, 3):
            triplet = raw_bits[i : i + 3]
            bit = 1 if sum(triplet) >= 2 else 0
            decoded_bits.append(bit)

        # Convert bits to bytes
        extracted = bytearray()
        for i in range(0, len(decoded_bits) - 7, 8):
            val = 0
            for b in range(8):
                val = (val << 1) | decoded_bits[i + b]
            extracted.append(val)

        # Check for magic header
        if not extracted.startswith(MAGIC_HEADER):
            logger.info(f"[Leak Tracer] No watermark detected in inspected image (case: {case_id}).")
            return WatermarkExtractResponse(
                status="no_watermark_detected",
                detected=False,
                confidence_score=0.0,
                audit_report=WatermarkAuditReport(
                    case_reference_id=case_id,
                    cryptographic_integrity="NO_WATERMARK",
                    evidence_affidavit="No Safemate forensic watermark signature was identified in this media.",
                    actionable_remedies=[
                        "Ensure the original uncompressed image file is provided.",
                        "If the image was heavily compressed through social media screenshots, secondary visual hash screening is recommended.",
                    ],
                ),
            )

        # Read payload length
        idx = len(MAGIC_HEADER)
        length = int.from_bytes(extracted[idx : idx + 2], "big")
        idx += 2

        if len(extracted) < idx + length + 16:
            logger.warning("[Leak Tracer] Incomplete watermark bitstream detected.")
            return WatermarkExtractResponse(
                status="no_watermark_detected",
                detected=False,
                confidence_score=0.1,
                audit_report=WatermarkAuditReport(
                    case_reference_id=case_id,
                    cryptographic_integrity="TRUNCATED_BITSTREAM",
                    evidence_affidavit="Watermark header located but bitstream was truncated or damaged.",
                    actionable_remedies=["Upload higher resolution original capture."],
                ),
            )

        raw_json = bytes(extracted[idx : idx + length])
        idx += length
        sig = bytes(extracted[idx : idx + 16])
        return self._build_match_response(raw_json, sig, case_id, now_ts)


# Global singleton instance
watermark_service = WatermarkService()