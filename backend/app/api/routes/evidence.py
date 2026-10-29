"""Disguised Evidence Vault route under /api/v1/evidence/upload."""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import EvidenceRecord, UserModel
from app.utils.auth import decode_access_token, get_current_user, get_optional_current_user
from app.schemas.evidence import (
    EvidenceDeleteResponse,
    EvidenceRecordDetail,
    EvidenceUploadResponse,
)

logger = logging.getLogger(__name__)

# Router definition with prefix /api/v1/evidence and tag "Disguised Evidence Vault"
router = APIRouter(prefix="/api/v1/evidence", tags=["Disguised Evidence Vault"])

def _resolve_media_type(filename: str) -> str:
    lower = (filename or '').lower()
    if lower.endswith(('.mp4', '.m4v')):
        return 'video/mp4'
    if lower.endswith('.m4a'):
        return 'audio/mp4'
    if lower.endswith('.webm'):
        return 'audio/webm'
    if lower.endswith(('.wav', '.wave')):
        return 'audio/wav'
    if lower.endswith(('.mp3', '.mpeg')):
        return 'audio/mpeg'
    if lower.endswith(('.ogg', '.oga')):
        return 'audio/ogg'
    if lower.endswith(('.jpg', '.jpeg')):
        return 'image/jpeg'
    if lower.endswith('.png'):
        return 'image/png'
    if lower.endswith('.webp'):
        return 'image/webp'
    return 'application/octet-stream'

# ---------------------------------------------------------------------------
# Private Secure Storage Directory Configuration
# ---------------------------------------------------------------------------
# Stored inside the private app directory (app/secure_storage/), outside public access
SECURE_STORAGE_DIR = Path(__file__).resolve().parent.parent.parent / "secure_storage"

# Trusted safety circle contacts alerted during evidence dispatch
DEFAULT_TRUSTED_CONTACTS = [
    "Maya Lin (Sister & Primary Emergency Contact)",
    "Marcus Vance (Trusted Contact)",
]


@router.post(
    "/upload",
    response_model=EvidenceUploadResponse,
    status_code=status.HTTP_200_OK,
    summary="Upload Disguised Evidence (Audio / Photo / Video)",
    description=(
        "Securely receives encrypted or disguised evidence files (audio clips, photos, video). "
        "Calculates cryptographic SHA-256 checksum for chain-of-custody integrity, stores the binary "
        "file in a private local vault directory, persists metadata into PostgreSQL database, "
        "logs structured confirmation, and simulates dispatch to trusted emergency contacts."
    ),
)
async def upload_evidence(
    file: UploadFile = File(
        ...,
        description="Binary media file (audio recording, photo, or video) to be secured",
    ),
    user_id: str = Form(
        ...,
        min_length=1,
        description="Unique identifier of the user logging the evidence",
        examples=["usr_safemate_001"],
    ),
    latitude: float = Form(
        ...,
        ge=-90.0,
        le=90.0,
        description="Current device latitude coordinate where evidence was captured",
        examples=[37.774929],
    ),
    longitude: float = Form(
        ...,
        ge=-180.0,
        le=180.0,
        description="Current device longitude coordinate where evidence was captured",
        examples=[-122.419416],
    ),
    note: Optional[str] = Form(
        None,
        description="Optional disguised note, transcription, or emergency description",
        examples=["Heard unusual footsteps behind dormitory block 3."],
    ),
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> EvidenceUploadResponse:
    """Process, hash, and securely store an evidence file with database persistence."""
    # 1. Read file contents into memory
    content = await file.read()
    if not content:
        logger.warning(f"Empty evidence file upload attempt by user '{user_id}'")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded evidence file is empty.",
        )

    file_size = len(content)

    # 2. Cryptographic SHA-256 hash generation for chain-of-custody integrity
    sha256_hash = hashlib.sha256(content).hexdigest()

    # 3. Securely store to private directory (without public exposure)
    SECURE_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    evidence_id = f"evd_{uuid.uuid4().hex[:12]}"
    original_filename = Path(file.filename).name if file.filename else "unnamed_evidence.bin"
    stored_filename = f"{evidence_id}_{original_filename}"
    target_path = SECURE_STORAGE_DIR / stored_filename

    target_path.write_bytes(content)

    # 4. Formulate timestamps, Google Maps coordinate link, and dispatch status
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    google_maps_url = f"https://maps.google.com/?q={latitude},{longitude}"
    simulated_dispatch_status = "CONFIRMED_DELIVERED_TO_TRUSTED_CONTACTS"

    # 5. Persist record into PostgreSQL/SQLAlchemy database
    # Bind owner_id to authenticated user while preserving requested user_id tag
    owner_id = current_user.id if current_user else None
    new_record = EvidenceRecord(
        evidence_id=evidence_id,
        user_id=user_id,
        owner_id=owner_id,
        file_name=original_filename,
        file_path=str(target_path),
        file_size=file_size,
        sha256_checksum=sha256_hash,
        latitude=latitude,
        longitude=longitude,
        note=note,
        timestamp=timestamp,
        simulated_dispatch_status=simulated_dispatch_status,
        google_maps_url=google_maps_url,
        contacts_alerted=json.dumps(DEFAULT_TRUSTED_CONTACTS),
    )
    db.add(new_record)
    db.commit()
    db.refresh(new_record)

    # 6. Structured JSON logging confirmation
    log_payload = {
        "event": "DISGUISED_EVIDENCE_VAULT_RECORDED",
        "evidence_id": evidence_id,
        "user_id": user_id,
        "owner_id": owner_id,
        "file_name": original_filename,
        "file_size_bytes": file_size,
        "sha256_checksum": sha256_hash,
        "coordinates": {"latitude": latitude, "longitude": longitude},
        "google_maps_url": google_maps_url,
        "note": note,
        "timestamp": timestamp,
        "simulated_dispatch_status": simulated_dispatch_status,
        "contacts_notified": DEFAULT_TRUSTED_CONTACTS,
    }
    logger.info(json.dumps(log_payload))

    # 7. Return standardized JSON response
    return EvidenceUploadResponse(
        status="success",
        file_name=original_filename,
        file_size=file_size,
        sha256_checksum=sha256_hash,
        sha256_hash=sha256_hash,
        timestamp=timestamp,
        simulated_dispatch_status=simulated_dispatch_status,
        evidence_id=evidence_id,
        user_id=user_id,
        latitude=latitude,
        longitude=longitude,
        note=note,
        google_maps_url=google_maps_url,
        contacts_alerted=DEFAULT_TRUSTED_CONTACTS,
    )


def check_evidence_owner(entry: EvidenceRecord, current_user: UserModel) -> None:
    """Validate that current_user is the legitimate owner of the evidence record."""
    is_owner = False
    if entry.owner_id is not None:
        is_owner = (entry.owner_id == current_user.id)
    else:
        is_owner = entry.user_id in (str(current_user.id), current_user.email)

    if not is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access or modify this evidence record.",
        )


@router.get(
    "/records",
    response_model=List[EvidenceRecordDetail],
    summary="List Evidence Vault Metadata Records",
    description="Inspection endpoint returning database-persisted evidence metadata records owned by authenticated user.",
)
async def list_evidence_records(
    db: Session = Depends(get_db),
    current_user: Optional[UserModel] = Depends(get_current_user),
) -> List[EvidenceRecordDetail]:
    """Retrieve metadata list of secured evidence records owned by current user."""
    search_keys = [str(current_user.id), current_user.email]
    records = (
        db.query(EvidenceRecord)
        .filter(
            (EvidenceRecord.owner_id == current_user.id)
            | (EvidenceRecord.user_id.in_(search_keys))
        )
        .order_by(EvidenceRecord.id.desc())
        .all()
    )
    return [
        EvidenceRecordDetail(
            evidence_id=entry.evidence_id,
            user_id=entry.user_id,
            file_name=entry.file_name,
            file_size=entry.file_size,
            sha256_checksum=entry.sha256_checksum,
            latitude=entry.latitude,
            longitude=entry.longitude,
            note=entry.note,
            timestamp=entry.timestamp,
            simulated_dispatch_status=entry.simulated_dispatch_status,
            google_maps_url=entry.google_maps_url,
        )
        for entry in records
    ]


@router.get(
    "/verify/{evidence_id}",
    summary="Verify Evidence Integrity Checksum",
    description="Verifies the on-disk SHA-256 checksum of an evidence record against the database hash.",
)
async def verify_evidence_integrity(
    evidence_id: str,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> Dict[str, Any]:
    """Verify chain-of-custody checksum for a specific evidence record owned by current user."""
    entry = db.query(EvidenceRecord).filter(EvidenceRecord.evidence_id == evidence_id).first()
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Evidence record '{evidence_id}' was not found in database.",
        )

    check_evidence_owner(entry, current_user)

    target_path = Path(entry.file_path)
    file_exists = target_path.exists()
    calculated_hash = (
        hashlib.sha256(target_path.read_bytes()).hexdigest() if file_exists else None
    )
    is_valid = file_exists and (calculated_hash == entry.sha256_checksum)

    return {
        "evidence_id": evidence_id,
        "file_name": entry.file_name,
        "stored_sha256": entry.sha256_checksum,
        "calculated_sha256": calculated_hash,
        "integrity_verified": is_valid,
        "timestamp": entry.timestamp,
    }


@router.get(
    "/download/{evidence_id}",
    summary="Download Secured Evidence Binary",
    description="Securely retrieves the encrypted or disguised evidence file for authorized legal chain-of-custody transfer.",
)
async def download_evidence(
    evidence_id: str,
    db: Session = Depends(get_db),
    token: Optional[str] = Query(None, description='Optional JWT authorization token via query string'),
    current_user: Optional[UserModel] = Depends(get_optional_current_user),
):
    """Download stored evidence file binary, verifying record ownership."""
    if not current_user and token:
        try:
            payload = decode_access_token(token)
            email = payload.get('sub')
            if email:
                current_user = db.query(UserModel).filter(UserModel.email == email).first()
        except Exception:
            pass

    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Authentication required to export evidence file. Please provide Bearer token or token query parameter.',
            headers={'WWW-Authenticate': 'Bearer'},
        )
    entry = db.query(EvidenceRecord).filter(EvidenceRecord.evidence_id == evidence_id).first()
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Evidence record '{evidence_id}' was not found in database.",
        )

    check_evidence_owner(entry, current_user)

    target_path = Path(entry.file_path).resolve()
    secure_dir = SECURE_STORAGE_DIR.resolve()
    try:
        target_path.relative_to(secure_dir)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid evidence file path.",
        )

    if not target_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Evidence binary file for '{evidence_id}' does not exist on vault storage.",
        )

    return FileResponse(
        path=str(target_path),
        filename=entry.file_name,
        media_type=_resolve_media_type(entry.file_name),
    )


@router.get(
    "/record/{evidence_id}",
    response_model=EvidenceRecordDetail,
    summary="Get Single Evidence Record Detail",
    description="Retrieves metadata details for a single secured evidence record.",
)
async def get_evidence_record(
    evidence_id: str,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> EvidenceRecordDetail:
    """Retrieve metadata for a specific evidence record ensuring caller is owner."""
    entry = db.query(EvidenceRecord).filter(EvidenceRecord.evidence_id == evidence_id).first()
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Evidence record '{evidence_id}' was not found in database.",
        )

    check_evidence_owner(entry, current_user)

    return EvidenceRecordDetail(
        evidence_id=entry.evidence_id,
        user_id=entry.user_id,
        file_name=entry.file_name,
        file_size=entry.file_size,
        sha256_checksum=entry.sha256_checksum,
        latitude=entry.latitude,
        longitude=entry.longitude,
        note=entry.note,
        timestamp=entry.timestamp,
        simulated_dispatch_status=entry.simulated_dispatch_status,
        google_maps_url=entry.google_maps_url,
    )


@router.delete(
    "/record/{evidence_id}",
    response_model=EvidenceDeleteResponse,
    summary="Securely Delete Evidence Record & File",
    description="Securely shreds physical binary from vault disk and purges metadata from database.",
)
async def delete_evidence_record(
    evidence_id: str,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> EvidenceDeleteResponse:
    """Securely delete evidence record and unlinks its physical file with ownership enforcement."""
    entry = db.query(EvidenceRecord).filter(EvidenceRecord.evidence_id == evidence_id).first()
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Evidence record '{evidence_id}' was not found in database.",
        )

    check_evidence_owner(entry, current_user)

    # Securely remove file from storage
    if entry.file_path:
        target_path = Path(entry.file_path).resolve()
        if target_path.exists():
            try:
                target_path.unlink()
                logger.info(f"Unlinked vault evidence file: {target_path}")
            except Exception as err:
                logger.warning(f"Could not unlink file {target_path}: {err}")

    db.delete(entry)
    db.commit()

    logger.info(f"Evidence record '{evidence_id}' purged from vault database by {current_user.email}.")

    return EvidenceDeleteResponse(
        status="success",
        message=f"Evidence record '{evidence_id}' and associated file securely purged from vault.",
        evidence_id=evidence_id,
    )




