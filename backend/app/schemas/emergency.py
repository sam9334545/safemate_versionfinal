"""Pydantic schemas for the Safemate / Aegis Emergency SOS & Trusted Contacts feature."""

from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Trusted Contacts Schemas
# ---------------------------------------------------------------------------
class ContactCreate(BaseModel):
    """Payload to register a new trusted emergency contact."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Full name of the trusted emergency contact",
        examples=["Maya Lin"],
    )
    phone_number: str = Field(
        ...,
        min_length=3,
        max_length=64,
        description="Phone number configured for emergency SMS/call dispatch",
        examples=["+1-555-0192"],
    )
    email: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Optional email address for emergency notifications",
        examples=["maya.lin@safemail.org"],
    )
    relationship: Optional[str] = Field(
        default=None,
        max_length=128,
        description="Relationship to user (e.g. Sister, Roommate, Campus Security)",
        examples=["Sister & Primary Emergency Contact"],
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "Maya Lin",
                "phone_number": "+1-555-0192",
                "email": "maya.lin@safemail.org",
                "relationship": "Sister & Primary Emergency Contact",
            }
        }
    }


class ContactResponse(BaseModel):
    """Public representation of a registered trusted contact."""

    id: int = Field(..., description="Unique database identifier for the contact")
    user_id: str = Field(..., description="ID or email of the user who registered this contact")
    name: str = Field(..., description="Full name of the contact")
    phone_number: str = Field(..., description="Contact phone number")
    email: Optional[str] = Field(default=None, description="Contact email address")
    relationship: Optional[str] = Field(default=None, description="Relationship to user")
    created_at: str = Field(..., description="ISO 8601 creation timestamp")

    model_config = {"from_attributes": True}


class ContactDeleteResponse(BaseModel):
    """Confirmation payload returned when a trusted contact is deleted."""

    status: str = Field(default="success", description="Status confirmation")
    message: str = Field(..., description="Human-readable success message")
    contact_id: int = Field(..., description="ID of the removed contact")


# ---------------------------------------------------------------------------
# Emergency SOS Panic Trigger Schemas
# ---------------------------------------------------------------------------
class EmergencyContact(BaseModel):
    """Schema representing an individual trusted emergency contact."""

    name: str = Field(..., description="Full name of the trusted emergency contact")
    phone: str = Field(..., description="Phone number configured for emergency alerts")
    relationship: Optional[str] = Field(
        default="Emergency Contact", description="Relationship to user (e.g., Sister, Roommate)"
    )
    email: Optional[str] = Field(default=None, description="Optional email address")


class NearestPoliceStationInfo(BaseModel):
    """Nearest police station assistance information resolved from live GPS."""

    name: str = Field(..., description="Name of the closest police station or chowki")
    distance_meters: float = Field(..., description="Straight-line distance in meters")
    distance_text: str = Field(..., description="Formatted distance string, e.g. '850m' or '2.1 km'")
    address: Optional[str] = Field(default=None, description="Street address or locality description")
    phone: Optional[str] = Field(default="112", description="Local station phone or helpline")
    emergency_dial: str = Field(default="112", description="Standard 1-tap emergency dial number")


class SOSRequest(BaseModel):
    """Request payload submitted when the emergency SOS panic button is pressed."""

    latitude: float = Field(
        ...,
        ge=-90.0,
        le=90.0,
        description="Current latitude coordinate of the device at the moment of distress",
        examples=[37.774929],
    )
    longitude: float = Field(
        ...,
        ge=-180.0,
        le=180.0,
        description="Current longitude coordinate of the device at the moment of distress",
        examples=[-122.419416],
    )
    message: Optional[str] = Field(
        default=None,
        description="Optional custom emergency message or situation note",
        examples=["I am feeling unsafe, please track my location immediately."],
    )
    user_id: Optional[str] = Field(
        default=None,
        description="Optional identifier of the user (auto-resolved when authenticated)",
        examples=["usr_safemate_001"],
    )
    timestamp: Optional[str] = Field(
        default=None,
        description="Optional client timestamp recorded when the panic button was activated",
        examples=["2026-10-24T16:27:38Z"],
    )
    client_contacts: Optional[List[EmergencyContact]] = Field(
        default=None,
        description="Optional list of trusted emergency contacts provided directly by the client",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "latitude": 37.774929,
                "longitude": -122.419416,
                "message": "I am feeling unsafe, please track my location immediately.",
                "user_id": "usr_safemate_001",
                "timestamp": "2026-10-24T16:27:38Z",
            }
        }
    }


class DispatchedAlertInfo(BaseModel):
    """Audit record of a dispatched alert notification to an individual contact."""

    name: str = Field(..., description="Name of the contact alerted")
    phone: str = Field(..., description="Phone number alerted")
    relationship: Optional[str] = Field(default=None, description="Relationship of the contact to the user")
    status: str = Field(default="DELIVERED", description="Dispatch transmission status")


class SOSResponse(BaseModel):
    """Response returned upon successful processing of an emergency SOS alert."""

    status: str = Field(default="success", description="Status confirmation of the dispatch")
    timestamp: str = Field(..., description="Timestamp of the alert dispatch")
    count: int = Field(..., description="Count of contacts successfully alerted")
    contacts_alerted: int = Field(
        ..., description="Count of contacts successfully alerted (semantic alias)"
    )
    alert_id: str = Field(..., description="Unique emergency dispatch incident identifier")
    user_id: str = Field(..., description="User ID associated with the SOS alert")
    google_maps_url: str = Field(..., description="Live Google Maps coordinates link")
    distress_message: str = Field(..., description="High-priority distress message broadcasted")
    contacts: List[DispatchedAlertInfo] = Field(
        default_factory=list,
        description="List of trusted contacts who received the emergency dispatch",
    )
    nearest_police: Optional[NearestPoliceStationInfo] = Field(
        default=None,
        description="Nearest police station or chowki identified from GPS coordinates",
    )


# ---------------------------------------------------------------------------
# Direct Voice Call Schemas
# ---------------------------------------------------------------------------
class DirectCallRequest(BaseModel):
    """Request to initiate a voice call to an emergency contact."""
    phone_number: str = Field(..., description="Destination phone number in E.164 format or standard dial format")
    contact_name: Optional[str] = Field("Emergency Contact", description="Contact name")
    message: Optional[str] = Field(None, description="Spoken alert message")
    user_id: Optional[str] = Field("usr_safemate_user", description="Identifier of the user")


class DirectCallResponse(BaseModel):
    """Response from direct call trigger endpoint."""
    status: str = Field(..., description="Status of call dispatch (e.g. INITIATED, SIMULATED, DEVICE_TEL_READY)")
    phone_number: str = Field(..., description="Target phone number")
    channel: str = Field(..., description="Delivery channel (e.g. twilio_voice, device_tel_protocol)")
    message: str = Field(..., description="Status explanation or spoken alert note")
    call_sid: Optional[str] = Field(None, description="Twilio Call SID if telephony provider is configured")
    tel_link: str = Field(..., description="Native device dialer link (tel: protocol)")


# ---------------------------------------------------------------------------
# Incident Lifecycle & Contact Update Schemas (100% Completeness)
# ---------------------------------------------------------------------------
class ContactUpdate(BaseModel):
    """Payload to update an existing trusted emergency contact."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=255, description="Updated contact name")
    phone_number: Optional[str] = Field(default=None, min_length=3, max_length=64, description="Updated phone number")
    email: Optional[str] = Field(default=None, max_length=255, description="Updated email address")
    relationship: Optional[str] = Field(default=None, max_length=128, description="Updated relationship")


class SOSResolveRequest(BaseModel):
    """Request payload to mark an active emergency alert as resolved or safe."""

    resolution_notes: Optional[str] = Field(
        default=None,
        description="Optional notes documenting situation de-escalation or responder resolution",
        examples=["User confirmed safe. Accompanied by campus escort."],
    )


class SOSResolveResponse(BaseModel):
    """Response confirming that an emergency alert has been resolved."""

    status: str = Field(default="resolved", description="Incident state")
    message: str = Field(
        default="Emergency alert successfully marked as resolved.",
        description="Resolution status confirmation message",
    )
    alert_id: str = Field(..., description="Alert ID")
    resolved_at: str = Field(..., description="ISO 8601 timestamp when the alert was resolved")
    resolution_notes: Optional[str] = Field(default=None, description="Resolution notes")


class SOSAlertDetail(BaseModel):
    """Full incident and audit record of an emergency dispatch."""

    alert_id: str = Field(..., description="Unique emergency incident identifier")
    user_id: str = Field(..., description="User ID associated with the alert")
    timestamp: str = Field(..., description="Timestamp when the alert was triggered")
    status: str = Field(..., description="Alert status ('ACTIVE', 'DELIVERED', 'RESOLVED', etc.)")
    latitude: float = Field(..., description="Latitude coordinate")
    longitude: float = Field(..., description="Longitude coordinate")
    distress_message: Optional[str] = Field(default=None, description="Distress message broadcasted")
    google_maps_url: Optional[str] = Field(default=None, description="Google Maps location link")
    contacts_alerted_count: int = Field(default=0, description="Total contacts alerted")
    resolved_at: Optional[str] = Field(default=None, description="Timestamp of resolution")
    resolution_notes: Optional[str] = Field(default=None, description="Incident notes upon resolution")
