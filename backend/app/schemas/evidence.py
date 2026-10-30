"""Pydantic schemas for the Disguised Evidence Vault service."""

from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


class EvidenceUploadResponse(BaseModel):
    """Response returned upon successful cryptographic archiving of disguised evidence."""

    status: str = Field(
        default="success",
        description="Status confirmation of the evidence storage and simulated dispatch",
    )
    file_name: str = Field(
        ...,
        description="Original name of the uploaded evidence file",
    )
    file_size: int = Field(
        ...,
        description="Total size of the uploaded evidence file in bytes",
    )
    sha256_checksum: str = Field(
        ...,
        description="Cryptographic SHA-256 hash of the file bytes for chain-of-custody verification",
    )
    sha256_hash: str = Field(
        ...,
        description="Semantic alias for sha256_checksum",
    )
    timestamp: str = Field(
        ...,
        description="ISO 8601 timestamp when evidence was received and recorded",
    )
    simulated_dispatch_status: str = Field(
        ...,
        description="Confirmation status of simulated evidence dispatch to trusted contacts",
    )
    evidence_id: str = Field(
        ...,
        description="Unique evidence tracking identifier",
    )
    user_id: str = Field(
        ...,
        description="Identifier of the user who recorded the evidence",
    )
    latitude: float = Field(
        ...,
        description="GPS latitude where evidence was captured",
    )
    longitude: float = Field(
        ...,
        description="GPS longitude where evidence was captured",
    )
    note: Optional[str] = Field(
        default=None,
        description="Optional contextual note or incident description",
    )
    google_maps_url: str = Field(
        ...,
        description="Google Maps coordinates link corresponding to the capture location",
    )
    contacts_alerted: List[str] = Field(
        default_factory=list,
        description="List of trusted safety contacts who received the simulated evidence alert",
    )


class EvidenceRecordDetail(BaseModel):
    """Metadata record of an archived piece of evidence."""

    evidence_id: str
    user_id: str
    file_name: str
    file_size: int
    sha256_checksum: str
    latitude: float
    longitude: float
    note: Optional[str] = None
    timestamp: str
    simulated_dispatch_status: str
    google_maps_url: str


class EvidenceDeleteResponse(BaseModel):
    """Response returned upon securely shredding and deleting an evidence record."""

    status: str = Field(default="success", description="Status confirmation")
    message: str = Field(..., description="Confirmation explanation message")
    evidence_id: str = Field(..., description="Unique identifier of the deleted evidence record")



class EvidenceCertificateResponse(BaseModel):
    """Institutional Section 63 BSA / 65B IEA forensic certificate of evidence."""
    certificate_id: str = Field(..., description="Unique legal certificate identifier")
    compliance_standard: str = Field(..., description="Statutory compliance standard")
    evidence_id: str = Field(..., description="Evidence tracking ID")
    file_name: str = Field(..., description="Evidence file name")
    file_size: int = Field(..., description="File size in bytes")
    sha256_checksum: str = Field(..., description="Cryptographic SHA-256 fingerprint")
    incident_timestamp: str = Field(..., description="Timestamp of evidence capture")
    certified_at: str = Field(..., description="Timestamp of certificate issuance")
    certified_by: str = Field(..., description="Authenticated certifier email or alias")
    certifier_id: str = Field(..., description="Authenticated certifier user ID")
    certifier_access_mode: str = Field(..., description="Session authentication mode")
    latitude: float = Field(..., description="Incident latitude coordinate")
    longitude: float = Field(..., description="Incident longitude coordinate")
    verification_hash: str = Field(..., description="HMAC-SHA256 seal verification signature")
    integrity_status: str = Field(..., description="Chain of custody integrity verdict")
    chain_of_custody_statement: str = Field(..., description="Statutory legal attestation statement")


class EvidenceSealResponse(BaseModel):
    """Cryptographic seal confirmation metadata for an evidence record."""
    status: str = Field(default="sealed", description="Seal status")
    evidence_id: str = Field(..., description="Evidence identifier")
    sealed_by: str = Field(..., description="Authenticated user email")
    sealed_by_id: str = Field(..., description="Authenticated user identifier")
    seal_timestamp: str = Field(..., description="Timestamp of cryptographic seal")
    sha256_checksum: str = Field(..., description="SHA-256 hash of original file")
    verification_hash: str = Field(..., description="HMAC-SHA256 verification hash")
    message: str = Field(..., description="Status summary message")
