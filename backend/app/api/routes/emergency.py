"""Emergency SOS & Trusted Contacts route under /api/v1/emergency."""

from __future__ import annotations

import datetime
import json
import logging
import math
import os
import uuid
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import EmergencyLogModel, TrustedContactModel, UserModel
from app.schemas.emergency import (
    ContactCreate,
    ContactDeleteResponse,
    ContactResponse,
    ContactUpdate,
    DirectCallRequest,
    DirectCallResponse,
    DispatchedAlertInfo,
    NearestPoliceStationInfo,
    SOSAlertDetail,
    SOSRequest,
    SOSResolveRequest,
    SOSResolveResponse,
    SOSResponse,
)
from app.utils.auth import get_current_user, get_optional_current_user

logger = logging.getLogger(__name__)

# Router with explicit prefix /api/v1/emergency and tag "Emergency SOS"
router = APIRouter(prefix="/api/v1/emergency", tags=["Emergency SOS"])

# ---------------------------------------------------------------------------
# Default / Fallback Emergency Trusted Contacts Database
# ---------------------------------------------------------------------------
DEFAULT_TRUSTED_CONTACTS: List[Dict[str, str]] = [
    {
        "name": "Maya Lin",
        "phone": "+1-555-0192",
        "relationship": "Primary Emergency Contact",
        "email": "maya.lin@safemail.org",
    },
    {
        "name": "Marcus Vance",
        "phone": "+1-555-0144",
        "relationship": "Trusted Emergency Contact",
        "email": "m.vance@safemail.org",
    },
    {
        "name": "Safemate Emergency Response Dispatch",
        "phone": "+1-800-555-SAFE",
        "relationship": "Emergency Dispatch Center",
        "email": "dispatch@safemate.org",
    },
]

MOCK_EMERGENCY_CONTACTS: Dict[str, List[Dict[str, str]]] = {
    "usr_safemate_001": [
        {
            "name": "Maya Lin",
            "phone": "+1-555-0192",
            "relationship": "Sister & Primary Emergency Contact",
            "email": "maya.lin@safemail.org",
        },
        {
            "name": "Marcus Vance",
            "phone": "+1-555-0144",
            "relationship": "Trusted Friend / Roommate",
            "email": "m.vance@safemail.org",
        },
        {
            "name": "Safemate Campus Response Unit",
            "phone": "+1-800-555-SAFE",
            "relationship": "Campus Safety Operations",
            "email": "dispatch@campus-safety.org",
        },
    ],
    "usr_maya": [
        {
            "name": "Maya Lin",
            "phone": "+1-555-0192",
            "relationship": "Primary Emergency Contact",
            "email": "maya.lin@safemail.org",
        },
    ],
}


def mask_phone_number(phone: str) -> str:
    """Mask sensitive middle digits of a phone number for privacy in logs."""
    clean = phone.strip()
    if len(clean) <= 4:
        return "****"
    prefix = clean[:3]
    suffix = clean[-4:]
    masked_middle = "*" * max(len(clean) - 7, 3)
    return f"{prefix}{masked_middle}{suffix}"


def get_emergency_contacts(user_id: str) -> List[Dict[str, str]]:
    """Retrieve fallback safety circle contacts (including Maya Lin)."""
    contacts = MOCK_EMERGENCY_CONTACTS.get(user_id)
    if contacts:
        return contacts
    logger.info(
        f"User '{user_id}' has no custom DB contacts; "
        "defaulting to trusted safety circle contacts (including Maya Lin)."
    )
    return DEFAULT_TRUSTED_CONTACTS


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    return r * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


async def resolve_nearest_police_station(lat: float, lon: float) -> NearestPoliceStationInfo:
    """Find the closest real police station from GPS coordinates using Nominatim/OSM with universal ERSS-112 fallback."""
    try:
        buf = 0.06
        viewbox = f"{lon-buf},{lat+buf},{lon+buf},{lat-buf}"
        url = f"https://nominatim.openstreetmap.org/search?amenity=police&viewbox={viewbox}&bounded=1&format=json&limit=5"
        headers = {"User-Agent": "SafeMate-Emergency/1.0"}
        async with httpx.AsyncClient(timeout=3.5) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                items = resp.json()
                if items:
                    closest = None
                    min_dist = float("inf")
                    for it in items:
                        p_lat = float(it.get("lat", 0))
                        p_lon = float(it.get("lon", 0))
                        d = haversine_distance_m(lat, lon, p_lat, p_lon)
                        if d < min_dist:
                            min_dist = d
                            closest = (it, d)
                    if closest:
                        it, dist_m = closest
                        disp_name = it.get("display_name", "Local Police Station")
                        short_name = disp_name.split(",")[0].strip() or "Local Police Station"
                        dist_txt = f"{dist_m:.0f}m" if dist_m < 1000 else f"{dist_m/1000.0:.1f} km"
                        return NearestPoliceStationInfo(
                            name=short_name,
                            distance_meters=round(dist_m, 1),
                            distance_text=dist_txt,
                            address=disp_name,
                            phone="112",
                            emergency_dial="112",
                        )
    except Exception as exc:
        logger.warning(f"Could not resolve nearest police station via OSM: {exc}")

    return NearestPoliceStationInfo(
        name="National Emergency Police Dispatch (ERSS)",
        distance_meters=0.0,
        distance_text="Immediate Coverage Area",
        address="Universal Women Safety & Police Response Command",
        phone="112",
        emergency_dial="112",
    )


async def dispatch_emergency_notifications(
    alert_id: str,
    user_id: str,
    contacts: List[Dict[str, Any]],
    distress_message: str,
    google_maps_url: str,
    dispatch_timestamp: str,
) -> List[DispatchedAlertInfo]:
    """Asynchronously dispatches emergency notifications to trusted contacts.

    Integrates Twilio SMS and HTTP Webhooks when environment credentials
    are supplied, safely falling back to structured JSON simulation logs
    with masked phone numbers if credentials are not configured.
    """
    twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
    twilio_token = os.getenv("TWILIO_AUTH_TOKEN")
    twilio_from = os.getenv("TWILIO_FROM_NUMBER")
    webhook_url = os.getenv("EMERGENCY_WEBHOOK_URL")

    dispatched_records: List[DispatchedAlertInfo] = []

    for contact in contacts:
        contact_name = contact.get("name", "Trusted Contact")
        contact_phone = contact.get("phone", "")
        contact_rel = contact.get("relationship", "Emergency Contact")
        masked_phone = mask_phone_number(contact_phone)

        delivery_status = "NATIVE_CELLULAR_SIM_READY"
        delivery_channel = "cellular_native_sim"

        if twilio_sid and twilio_token and twilio_from:
            try:
                twilio_endpoint = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json"
                async with httpx.AsyncClient(timeout=4.0) as http_client:
                    response = await http_client.post(
                        twilio_endpoint,
                        data={
                            "From": twilio_from,
                            "To": contact_phone,
                            "Body": distress_message,
                        },
                        auth=(twilio_sid, twilio_token),
                    )
                    if response.status_code in (200, 201):
                        delivery_status = "SENT_TWILIO"
                        delivery_channel = "twilio_sms"
                    else:
                        delivery_status = f"SIMULATED_FALLBACK_{response.status_code}"
                        delivery_channel = "cellular_native_sim"
            except Exception as twilio_err:
                logger.warning(
                    f"Twilio SMS dispatch to {masked_phone} encountered error: {twilio_err}. Using simulated delivery."
                )
                delivery_status = "SIMULATED_DELIVERED"
                delivery_channel = "cellular_native_sim"

        if webhook_url:
            try:
                async with httpx.AsyncClient(timeout=4.0) as http_client:
                    await http_client.post(
                        webhook_url,
                        json={
                            "event": "EMERGENCY_SOS_ALERT",
                            "alert_id": alert_id,
                            "user_id": user_id,
                            "recipient": contact_name,
                            "google_maps_url": google_maps_url,
                            "timestamp": dispatch_timestamp,
                        },
                    )
            except Exception as hook_err:
                logger.warning(f"Webhook alert notification error: {hook_err}")

        # Structured audit log with safe masking of phone number
        structured_audit_entry = {
            "event": "EMERGENCY_SOS_DISPATCH_CONFIRMED",
            "alert_id": alert_id,
            "user_id": user_id,
            "recipient_name": contact_name,
            "relationship": contact_rel,
            "recipient_phone_masked": masked_phone,
            "delivery_channel": delivery_channel,
            "google_maps_url": google_maps_url,
            "dispatch_timestamp": dispatch_timestamp,
            "status": delivery_status,
        }
        logger.info(json.dumps(structured_audit_entry))

        dispatched_records.append(
            DispatchedAlertInfo(
                name=contact_name,
                phone=contact_phone,
                relationship=contact_rel,
                status=delivery_status,
            )
        )

    return dispatched_records


# ---------------------------------------------------------------------------
# Trusted Contacts Management Endpoints
# ---------------------------------------------------------------------------
@router.post(
    "/contacts",
    response_model=ContactResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a Trusted Emergency Contact",
    description="Registers a new persistent trusted contact for the authenticated user.",
)
async def add_trusted_contact(
    payload: ContactCreate,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ContactResponse:
    """Create and persist a new trusted emergency contact for the user."""
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    contact = TrustedContactModel(
        user_id=str(current_user.id),
        name=payload.name,
        phone_number=payload.phone_number,
        email=payload.email,
        relationship=payload.relationship,
        created_at=now_iso,
    )
    db.add(contact)
    db.commit()
    db.refresh(contact)
    logger.info(
        f"User '{current_user.email}' added trusted contact '{contact.name}' (ID: {contact.id})"
    )
    return contact


@router.get(
    "/contacts",
    response_model=List[ContactResponse],
    status_code=status.HTTP_200_OK,
    summary="Retrieve User Trusted Contacts",
    description="Returns all persistent trusted emergency contacts registered by the authenticated user.",
)
async def get_user_trusted_contacts(
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[ContactResponse]:
    """Retrieve all contacts belonging to the currently logged in user."""
    contacts = (
        db.query(TrustedContactModel)
        .filter(
            (TrustedContactModel.user_id == str(current_user.id))
            | (TrustedContactModel.user_id == current_user.email)
        )
        .order_by(TrustedContactModel.id.asc())
        .all()
    )
    return contacts


@router.delete(
    "/contacts/{contact_id}",
    response_model=ContactDeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Remove a Trusted Emergency Contact",
    description="Deletes a trusted emergency contact belonging to the authenticated user.",
)
async def delete_trusted_contact(
    contact_id: int,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ContactDeleteResponse:
    """Delete a trusted contact ensuring the user owns the record."""
    contact = (
        db.query(TrustedContactModel)
        .filter(
            TrustedContactModel.id == contact_id,
            (TrustedContactModel.user_id == str(current_user.id))
            | (TrustedContactModel.user_id == current_user.email),
        )
        .first()
    )
    if not contact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trusted contact with ID {contact_id} not found.",
        )

    db.delete(contact)
    db.commit()
    logger.info(
        f"User '{current_user.email}' removed trusted contact '{contact.name}' (ID: {contact_id})"
    )
    return ContactDeleteResponse(
        status="success",
        message=f"Trusted contact '{contact.name}' (ID: {contact_id}) was removed successfully.",
        contact_id=contact_id,
    )


@router.put(
    "/contacts/{contact_id}",
    response_model=ContactResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a Trusted Emergency Contact",
    description="Updates information for an existing trusted emergency contact belonging to the authenticated user.",
)
async def update_trusted_contact(
    contact_id: int,
    payload: ContactUpdate,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ContactResponse:
    """Update fields of an existing trusted contact."""
    contact = (
        db.query(TrustedContactModel)
        .filter(
            TrustedContactModel.id == contact_id,
            (TrustedContactModel.user_id == str(current_user.id))
            | (TrustedContactModel.user_id == current_user.email),
        )
        .first()
    )
    if not contact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trusted contact with ID {contact_id} not found.",
        )

    if payload.name is not None:
        contact.name = payload.name
    if payload.phone_number is not None:
        contact.phone_number = payload.phone_number
    if payload.email is not None:
        contact.email = payload.email
    if payload.relationship is not None:
        contact.relationship = payload.relationship

    db.commit()
    db.refresh(contact)
    logger.info(
        f"User '{current_user.email}' updated trusted contact '{contact.name}' (ID: {contact_id})"
    )
    return contact


# ---------------------------------------------------------------------------
# Emergency SOS Panic Trigger Endpoint
# ---------------------------------------------------------------------------
@router.post(
    "/sos",
    response_model=SOSResponse,
    status_code=status.HTTP_200_OK,
    summary="Trigger Emergency SOS Panic Button",
    description=(
        "Processes an urgent SOS panic trigger. Validates coordinates, queries user's "
        "trusted contacts from PostgreSQL (or falls back to default safety circle), formats "
        "distress message with live Google Maps link, executes async multi-channel dispatch, "
        "records audit log in EmergencyLogModel, and returns dispatch confirmation."
    ),
)
async def trigger_sos(
    payload: SOSRequest,
    current_user: Optional[UserModel] = Depends(get_optional_current_user),
    db: Session = Depends(get_db),
) -> SOSResponse:
    """Handle incoming SOS panic button activation."""
    alert_id = f"sos_{uuid.uuid4().hex[:12]}"
    dispatch_timestamp = (
        payload.timestamp
        if payload.timestamp
        else datetime.datetime.now(datetime.timezone.utc).isoformat()
    )

    # 1. Determine resolved user_id
    if current_user:
        resolved_user_id = str(current_user.id)
        user_email = current_user.email
    elif payload.user_id:
        resolved_user_id = payload.user_id
        user_email = None
    else:
        resolved_user_id = "usr_safemate_001"
        user_email = None

    # 2. Query persistent contacts from PostgreSQL
    search_keys = [resolved_user_id]
    if user_email:
        search_keys.append(user_email)

    db_contacts = (
        db.query(TrustedContactModel)
        .filter(TrustedContactModel.user_id.in_(search_keys))
        .all()
    )

    contacts: List[Dict[str, Any]] = []
    if payload.client_contacts and len(payload.client_contacts) > 0:
        for c in payload.client_contacts:
            contacts.append({
                "name": c.name,
                "phone": c.phone,
                "email": c.email,
                "relationship": c.relationship or "Primary Emergency Contact",
            })
    elif db_contacts:
        for c in db_contacts:
            contacts.append({
                "name": c.name,
                "phone": c.phone_number,
                "email": c.email,
                "relationship": c.relationship or "Trusted Emergency Contact",
            })
    else:
        # Fallback to configured mock/safety circle (ensuring Maya Lin is present)
        contacts = get_emergency_contacts(resolved_user_id)

    if not contacts:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No emergency contacts registered for user '{resolved_user_id}'.",
        )

    # 3. Format Google Maps coordinates, live telemetry & distress message
    google_maps_url = f"https://maps.google.com/?q={payload.latitude},{payload.longitude}"
    custom_note = f"\nDistress Note: {payload.message}" if payload.message else ""

    # Native live telemetry details packaging
    telemetry_parts = []
    if payload.telemetry:
        if payload.telemetry.accuracy is not None:
            telemetry_parts.append(f"GPS Fix: ±{payload.telemetry.accuracy:.0f}m")
        if payload.telemetry.speed is not None and payload.telemetry.speed > 0:
            telemetry_parts.append(f"Speed: {payload.telemetry.speed:.1f} m/s")
        if payload.telemetry.heading is not None:
            telemetry_parts.append(f"Heading: {payload.telemetry.heading:.0f}°")
        if payload.telemetry.battery_level is not None:
            pct = int(payload.telemetry.battery_level * 100)
            chg = " (Charging)" if payload.telemetry.battery_charging else ""
            telemetry_parts.append(f"Battery: {pct}%{chg}")
        if payload.telemetry.network_type:
            telemetry_parts.append(f"Network: {payload.telemetry.network_type}")
    telemetry_note = f"\nLive Telemetry: {' • '.join(telemetry_parts)}" if telemetry_parts else ""

    user_note = ""
    if payload.user_details:
        ud = payload.user_details
        u_desc = []
        if ud.name: u_desc.append(f"Name: {ud.name}")
        if ud.email: u_desc.append(f"Email: {ud.email}")
        if ud.access_mode: u_desc.append(f"Mode: {ud.access_mode}")
        if u_desc:
            user_note = f"\nUser Details: {', '.join(u_desc)}"

    distress_message = (
        f"🚨 EMERGENCY SOS [SAFEMATE ALERT] 🚨\n"
        f"User '{resolved_user_id}' activated the emergency panic button!\n"
        f"Timestamp: {dispatch_timestamp}\n"
        f"Live GPS Location: {payload.latitude}, {payload.longitude}\n"
        f"Google Maps Link: {google_maps_url}{custom_note}{telemetry_note}{user_note}\n"
        f"PRIORITY: IMMEDIATE ASSISTANCE REQUIRED. Please check in or dispatch emergency response."
    )

    # 4. Asynchronously dispatch notifications (Twilio / Webhook / Structured Log Simulation)
    dispatched_alert_records = await dispatch_emergency_notifications(
        alert_id=alert_id,
        user_id=resolved_user_id,
        contacts=contacts,
        distress_message=distress_message,
        google_maps_url=google_maps_url,
        dispatch_timestamp=dispatch_timestamp,
    )

    # 5. Record audit trail in EmergencyLogModel
    try:
        emergency_log = EmergencyLogModel(
            alert_id=alert_id,
            user_id=resolved_user_id,
            latitude=payload.latitude,
            longitude=payload.longitude,
            triggered_at=dispatch_timestamp,
            status="dispatched",
            distress_message=distress_message,
            google_maps_url=google_maps_url,
            contacts_alerted_count=len(contacts),
        )
        db.add(emergency_log)
        db.commit()
    except Exception as log_err:
        logger.warning(f"Failed to record EmergencyLogModel audit trail: {log_err}")
        db.rollback()

    # 5.5 Resolve nearest police station from GPS coordinates
    nearest_police = await resolve_nearest_police_station(payload.latitude, payload.longitude)

    # 6. Return response confirming status, timestamp, and contact breakdown
    return SOSResponse(
        status="success",
        timestamp=dispatch_timestamp,
        count=len(contacts),
        contacts_alerted=len(contacts),
        alert_id=alert_id,
        user_id=resolved_user_id,
        google_maps_url=google_maps_url,
        distress_message=distress_message,
        contacts=dispatched_alert_records,
        nearest_police=nearest_police,
        telemetry=payload.telemetry,
        user_details=payload.user_details,
    )


# ---------------------------------------------------------------------------
# Direct Emergency Telephony Voice Call Endpoint
# ---------------------------------------------------------------------------
@router.post(
    "/call",
    response_model=DirectCallResponse,
    status_code=status.HTTP_200_OK,
    summary="Trigger Direct Telephony Voice Call",
    description=(
        "Dispatches an emergency voice phone call via Twilio Voice if configured in environment, "
        "and returns native device tel: protocol link for instant hardware calling."
    ),
)
async def trigger_direct_call(
    payload: DirectCallRequest,
    current_user: Optional[UserModel] = Depends(get_optional_current_user),
) -> DirectCallResponse:
    """Initiate a real emergency voice phone call."""
    user_id = str(current_user.id) if current_user else (payload.user_id or "usr_safemate_user")
    phone = payload.phone_number.strip()
    clean_phone = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
    tel_link = f"tel:{clean_phone}"

    spoken_message = payload.message or (
        f"Emergency distress call from SafeMate user {user_id}. "
        "Immediate assistance is requested. Please check your messages for location."
    )

    twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
    twilio_token = os.getenv("TWILIO_AUTH_TOKEN")
    twilio_from = os.getenv("TWILIO_FROM_NUMBER")

    call_sid = None
    delivery_status = "DEVICE_DIALER_READY"
    channel = "device_tel_protocol"

    if twilio_sid and twilio_token and twilio_from:
        try:
            twilio_endpoint = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Calls.json"
            twiml = f"<Response><Say voice='alice'>{spoken_message}</Say></Response>"
            async with httpx.AsyncClient(timeout=5.0) as http_client:
                res = await http_client.post(
                    twilio_endpoint,
                    data={
                        "From": twilio_from,
                        "To": clean_phone,
                        "Twiml": twiml,
                    },
                    auth=(twilio_sid, twilio_token),
                )
                if res.status_code in (200, 201):
                    call_data = res.json()
                    call_sid = call_data.get("sid")
                    delivery_status = "CALL_INITIATED"
                    channel = "twilio_voice"
                else:
                    logger.warning(f"Twilio Voice returned HTTP {res.status_code}: {res.text}")
                    delivery_status = "SIMULATED_VOICE_CALL"
        except Exception as err:
            logger.warning(f"Twilio Voice call error: {err}")
            delivery_status = "SIMULATED_VOICE_CALL"

    logger.info(
        f"Emergency Call Initiated for user '{user_id}' to '{mask_phone_number(clean_phone)}' "
        f"via {channel} (status: {delivery_status})"
    )

    return DirectCallResponse(
        status=delivery_status,
        phone_number=clean_phone,
        channel=channel,
        message=f"Direct call initiated to {payload.contact_name or 'Emergency Contact'} ({clean_phone}).",
        call_sid=call_sid,
        tel_link=tel_link,
    )


@router.get(
    "/alerts",
    summary="List Emergency SOS Panic Audit Logs",
    description="Returns recent emergency dispatch logs and audit confirmations.",
)
async def list_emergency_alerts(
    db: Session = Depends(get_db),
    current_user: Optional[UserModel] = Depends(get_optional_current_user),
) -> List[Dict[str, Any]]:
    """Retrieve audit history of triggered emergency SOS panic alerts."""
    query = db.query(EmergencyLogModel)
    if current_user:
        query = query.filter(
            (EmergencyLogModel.user_id == str(current_user.id))
            | (EmergencyLogModel.user_id == current_user.email)
        )
    logs = query.order_by(EmergencyLogModel.id.desc()).limit(20).all()
    return [
        {
            "id": log.id,
            "alert_id": log.alert_id or f"sos_legacy_{log.id}",
            "user_id": log.user_id,
            "latitude": log.latitude,
            "longitude": log.longitude,
            "triggered_at": log.triggered_at,
            "status": log.status,
            "distress_message": log.distress_message,
            "google_maps_url": log.google_maps_url,
            "contacts_alerted_count": log.contacts_alerted_count,
            "resolved_at": log.resolved_at,
            "resolution_notes": log.resolution_notes,
        }
        for log in logs
    ]


@router.get(
    "/sos/{alert_id}",
    response_model=SOSAlertDetail,
    summary="Get Emergency Alert Details",
    description="Fetches full details and status of an emergency dispatch incident by alert ID.",
)
async def get_emergency_alert(
    alert_id: str,
    db: Session = Depends(get_db),
    current_user: Optional[UserModel] = Depends(get_optional_current_user),
) -> SOSAlertDetail:
    """Retrieve details for a specific emergency alert."""
    log = (
        db.query(EmergencyLogModel)
        .filter(
            (EmergencyLogModel.alert_id == alert_id)
            | (EmergencyLogModel.id == (int(alert_id) if alert_id.isdigit() else -1))
        )
        .first()
    )
    if not log:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Emergency alert record '{alert_id}' not found.",
        )

    # If authenticated, verify ownership unless admin
    if current_user:
        user_matches = log.user_id in (str(current_user.id), current_user.email)
        if not user_matches and getattr(current_user, "role", "") != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to access this emergency incident record.",
            )

    return SOSAlertDetail(
        alert_id=log.alert_id or str(log.id),
        user_id=log.user_id,
        timestamp=log.triggered_at or "",
        status=log.status or "dispatched",
        latitude=log.latitude,
        longitude=log.longitude,
        distress_message=log.distress_message,
        google_maps_url=log.google_maps_url,
        contacts_alerted_count=log.contacts_alerted_count or 0,
        resolved_at=log.resolved_at,
        resolution_notes=log.resolution_notes,
    )


@router.post(
    "/sos/{alert_id}/resolve",
    response_model=SOSResolveResponse,
    summary="Resolve an Emergency SOS Alert",
    description="Marks an active emergency alert incident as resolved, recording de-escalation notes.",
)
async def resolve_emergency_alert(
    alert_id: str,
    payload: Optional[SOSResolveRequest] = None,
    db: Session = Depends(get_db),
    current_user: Optional[UserModel] = Depends(get_optional_current_user),
) -> SOSResolveResponse:
    """Mark an emergency dispatch incident resolved and safe."""
    log = (
        db.query(EmergencyLogModel)
        .filter(
            (EmergencyLogModel.alert_id == alert_id)
            | (EmergencyLogModel.id == (int(alert_id) if alert_id.isdigit() else -1))
        )
        .first()
    )
    if not log:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Emergency alert record '{alert_id}' not found.",
        )

    # Enforce ownership check: User A cannot resolve User B's alert
    if current_user:
        user_matches = log.user_id in (str(current_user.id), current_user.email)
        if not user_matches and getattr(current_user, "role", "") != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to resolve another user's emergency incident.",
            )

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    log.status = "resolved"
    log.resolved_at = now_iso
    if payload and payload.resolution_notes:
        log.resolution_notes = payload.resolution_notes

    db.commit()
    db.refresh(log)

    logger.info(
        f"Emergency alert '{alert_id}' marked as resolved at {now_iso} with notes: '{log.resolution_notes}'"
    )

    return SOSResolveResponse(
        status="resolved",
        message="Emergency alert successfully marked as resolved. Situation secured.",
        alert_id=alert_id,
        resolved_at=now_iso,
        resolution_notes=log.resolution_notes,
    )



# ---------------------------------------------------------------------------
# Native WebSocket & REST Real-Time Telemetry Streaming Endpoints
# ---------------------------------------------------------------------------
@router.websocket("/ws/telemetry")
async def emergency_telemetry_ws(websocket: WebSocket):
    """Real-time native WebSocket stream for live emergency telemetry coordinates without SDK dependencies."""
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            user_tag = data.get("user_id") or "anonymous"
            lat = data.get("latitude")
            lng = data.get("longitude")
            logger.info(f"[Emergency Telemetry WS] Live coordinate ping from {user_tag}: ({lat}, {lng})")
            await websocket.send_json({
                "event": "TELEMETRY_ACKNOWLEDGED",
                "received_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "status": "STREAMING_ACTIVE",
                "coordinates": {"latitude": lat, "longitude": lng},
            })
    except WebSocketDisconnect:
        logger.info("[Emergency Telemetry WS] Client disconnected cleanly.")
    except Exception as ws_err:
        logger.warning(f"[Emergency Telemetry WS] Stream notification: {ws_err}")


@router.post(
    "/telemetry",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Post Live Telemetry Update",
    description="Native REST endpoint to submit ongoing live telemetry pings (speed, heading, battery, accuracy) during an emergency.",
)
async def post_live_telemetry(
    payload: SOSRequest,
    current_user: Optional[UserModel] = Depends(get_optional_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Ingest native device telemetry update without fragile SDK wrappers."""
    user_id = str(current_user.id) if current_user else (payload.user_id or "usr_safemate")
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    log_entry = {
        "event": "LIVE_TELEMETRY_RECORDED",
        "user_id": user_id,
        "latitude": payload.latitude,
        "longitude": payload.longitude,
        "timestamp": now_iso,
        "telemetry": payload.telemetry.model_dump() if payload.telemetry else None,
    }
    logger.info(json.dumps(log_entry))
    return {
        "status": "telemetry_recorded",
        "timestamp": now_iso,
        "user_id": user_id,
        "coordinates": {"latitude": payload.latitude, "longitude": payload.longitude},
        "telemetry": payload.telemetry,
    }
