"""Unit and integration tests for the Emergency SOS & Trusted Contacts features."""

from __future__ import annotations

import datetime
import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import EmergencyLogModel, TrustedContactModel, UserModel
from app.utils.auth import create_access_token


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def test_user():
    """Ensure a test user exists in DB and return their auth headers and info."""
    email = "emergency_tester@safemate.org"
    with SessionLocal() as db:
        user = db.query(UserModel).filter(UserModel.email == email).first()
        if not user:
            user = UserModel(
                email=email,
                hashed_password="mock_hashed_password",
                created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                is_active=True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        user_id = user.id

    token = create_access_token(data={"sub": email, "user_id": user_id})
    return {
        "id": user_id,
        "email": email,
        "headers": {"Authorization": f"Bearer {token}"},
    }


def test_emergency_routes_in_openapi(client):
    """Verify emergency SOS and contacts endpoints are registered in OpenAPI."""
    openapi = app.openapi()
    paths = openapi["paths"]
    assert "/api/v1/emergency/sos" in paths
    assert "/api/v1/emergency/contacts" in paths
    assert "/api/v1/emergency/contacts/{contact_id}" in paths

    assert "Emergency SOS" in paths["/api/v1/emergency/sos"]["post"]["tags"]
    assert "Emergency SOS" in paths["/api/v1/emergency/contacts"]["post"]["tags"]
    assert "Emergency SOS" in paths["/api/v1/emergency/contacts"]["get"]["tags"]
    assert "Emergency SOS" in paths["/api/v1/emergency/contacts/{contact_id}"]["delete"]["tags"]


def test_emergency_sos_trigger_success_with_timestamp(client):
    """Verify triggering SOS with timestamp alerts contacts including Maya Lin."""
    payload = {
        "user_id": "usr_safemate_001",
        "latitude": 37.774929,
        "longitude": -122.419416,
        "timestamp": "2026-10-24T17:01:00Z",
    }
    response = client.post("/api/v1/emergency/sos", json=payload)
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "success"
    assert data["timestamp"] == "2026-10-24T17:01:00Z"
    assert data["count"] >= 1
    assert data["contacts_alerted"] == data["count"]
    assert "https://maps.google.com/?q=37.774929,-122.419416" in data["google_maps_url"]
    assert any(c["name"] == "Maya Lin" for c in data["contacts"])


def test_emergency_sos_trigger_optional_timestamp(client):
    """Verify triggering SOS without timestamp auto-populates timestamp."""
    payload = {
        "user_id": "usr_safemate_999",
        "latitude": 40.712776,
        "longitude": -74.005974,
    }
    response = client.post("/api/v1/emergency/sos", json=payload)
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "success"
    assert data["timestamp"] is not None
    assert any(c["name"] == "Maya Lin" for c in data["contacts"])


def test_emergency_sos_validation_failure(client):
    """Verify invalid coordinates produce validation errors (HTTP 422)."""
    # Missing longitude
    response = client.post("/api/v1/emergency/sos", json={
        "user_id": "usr_safemate_001",
        "latitude": 37.774929,
    })
    assert response.status_code == 422

    # Out-of-bounds latitude
    response = client.post("/api/v1/emergency/sos", json={
        "user_id": "usr_safemate_001",
        "latitude": 100.0,
        "longitude": -122.419416,
    })
    assert response.status_code == 422


def test_root_lists_emergency_endpoints(client):
    """Verify root endpoint advertises emergency links."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["emergency_sos"] == "/api/v1/emergency/sos"
    assert data["emergency_contacts"] == "/api/v1/emergency/contacts"


def test_add_trusted_contact_success(client, test_user):
    """Verify adding a trusted contact persists to DB and returns 201."""
    payload = {
        "name": "Sarah Connor",
        "phone_number": "+1-555-8932",
        "email": "sarah.connor@safemail.org",
        "relationship": "Guardian & Emergency Responder",
    }
    response = client.post(
        "/api/v1/emergency/contacts",
        json=payload,
        headers=test_user["headers"],
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Sarah Connor"
    assert data["phone_number"] == "+1-555-8932"
    assert data["email"] == "sarah.connor@safemail.org"
    assert data["relationship"] == "Guardian & Emergency Responder"
    assert "id" in data
    assert "created_at" in data

    # Verify directly in database
    with SessionLocal() as db:
        record = db.query(TrustedContactModel).filter(TrustedContactModel.id == data["id"]).first()
        assert record is not None
        assert record.name == "Sarah Connor"


def test_add_trusted_contact_unauthenticated_fails(client):
    """Verify unauthenticated request to add contact returns 401."""
    payload = {
        "name": "Alex Mercer",
        "phone_number": "+1-555-3210",
    }
    response = client.post("/api/v1/emergency/contacts", json=payload)
    assert response.status_code == 401


def test_add_trusted_contact_validation_error(client, test_user):
    """Verify missing required phone_number returns 422."""
    payload = {
        "name": "Alex Mercer",
    }
    response = client.post(
        "/api/v1/emergency/contacts",
        json=payload,
        headers=test_user["headers"],
    )
    assert response.status_code == 422


def test_get_trusted_contacts(client, test_user):
    """Verify retrieving trusted contacts returns list belonging to authenticated user."""
    # Ensure at least one contact exists
    contact_payload = {
        "name": "Elena Fisher",
        "phone_number": "+1-555-4421",
        "email": "elena@safemail.org",
    }
    client.post(
        "/api/v1/emergency/contacts",
        json=contact_payload,
        headers=test_user["headers"],
    )

    response = client.get("/api/v1/emergency/contacts", headers=test_user["headers"])
    assert response.status_code == 200
    contacts = response.json()
    assert isinstance(contacts, list)
    assert len(contacts) >= 1
    assert any(c["name"] == "Elena Fisher" for c in contacts)


def test_get_trusted_contacts_unauthenticated_fails(client):
    """Verify unauthenticated request to get contacts returns 401."""
    response = client.get("/api/v1/emergency/contacts")
    assert response.status_code == 401


def test_delete_trusted_contact_success(client, test_user):
    """Verify deleting a trusted contact removes it from DB."""
    # 1. Create a contact to delete
    create_res = client.post(
        "/api/v1/emergency/contacts",
        json={"name": "Temporary Contact", "phone_number": "+1-555-9999"},
        headers=test_user["headers"],
    )
    contact_id = create_res.json()["id"]

    # 2. Delete the contact
    del_res = client.delete(
        f"/api/v1/emergency/contacts/{contact_id}",
        headers=test_user["headers"],
    )
    assert del_res.status_code == 200
    del_data = del_res.json()
    assert del_data["status"] == "success"
    assert del_data["contact_id"] == contact_id

    # 3. Verify it is gone
    with SessionLocal() as db:
        record = db.query(TrustedContactModel).filter(TrustedContactModel.id == contact_id).first()
        assert record is None

    # 4. Deleting again returns 404
    del_again = client.delete(
        f"/api/v1/emergency/contacts/{contact_id}",
        headers=test_user["headers"],
    )
    assert del_again.status_code == 404


def test_delete_nonexistent_contact_returns_404(client, test_user):
    """Verify deleting non-existent contact returns 404."""
    response = client.delete("/api/v1/emergency/contacts/9999999", headers=test_user["headers"])
    assert response.status_code == 404


def test_sos_with_authenticated_user_custom_contacts(client, test_user):
    """Verify SOS dispatch alerts custom DB contacts and writes to EmergencyLogModel."""
    # 1. Add specific custom contact
    unique_name = f"Chief Responder {datetime.datetime.now().microsecond}"
    client.post(
        "/api/v1/emergency/contacts",
        json={
            "name": unique_name,
            "phone_number": "+1-555-7777",
            "email": "chief@safety.org",
            "relationship": "Lead Safety Officer",
        },
        headers=test_user["headers"],
    )

    # 2. Trigger SOS with authenticated token and optional custom message
    sos_payload = {
        "latitude": 37.774929,
        "longitude": -122.419416,
        "message": "Assistance needed near East Quad Gate.",
    }
    response = client.post(
        "/api/v1/emergency/sos",
        json=sos_payload,
        headers=test_user["headers"],
    )
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "success"
    assert "Assistance needed near East Quad Gate." in data["distress_message"]
    assert any(c["name"] == unique_name for c in data["contacts"])

    # 3. Verify audit record in EmergencyLogModel
    with SessionLocal() as db:
        log_entry = (
            db.query(EmergencyLogModel)
            .filter(EmergencyLogModel.user_id == str(test_user["id"]))
            .order_by(EmergencyLogModel.id.desc())
            .first()
        )
        assert log_entry is not None
        assert log_entry.latitude == 37.774929
        assert log_entry.longitude == -122.419416
        assert log_entry.status == "dispatched"
        assert "East Quad Gate" in log_entry.distress_message


def test_list_emergency_alerts(client, test_user):
    """Verify querying emergency SOS audit logs endpoint."""
    res = client.get("/api/v1/emergency/alerts", headers=test_user["headers"])
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    if len(data) > 0:
        alert = data[0]
        assert "id" in alert
        assert "latitude" in alert
        assert "longitude" in alert
        assert "triggered_at" in alert
        assert "status" in alert


def test_update_trusted_contact(client, test_user):
    """Verify editing an existing trusted contact via PUT."""
    # 1. Create a contact
    create_res = client.post(
        "/api/v1/emergency/contacts",
        json={
            "name": "Original Name",
            "phone_number": "+1-555-8888",
            "relationship": "Roommate",
        },
        headers=test_user["headers"],
    )
    assert create_res.status_code == 201
    contact_id = create_res.json()["id"]

    # 2. Update contact
    update_res = client.put(
        f"/api/v1/emergency/contacts/{contact_id}",
        json={
            "name": "Updated Safe Contact",
            "relationship": "Primary Guardian",
        },
        headers=test_user["headers"],
    )
    assert update_res.status_code == 200
    updated_data = update_res.json()
    assert updated_data["name"] == "Updated Safe Contact"
    assert updated_data["relationship"] == "Primary Guardian"
    assert updated_data["phone_number"] == "+1-555-8888"


def test_emergency_alert_lifecycle_and_resolution(client, test_user):
    """Verify triggering an alert, fetching it by ID, and resolving it with notes."""
    # 1. Trigger SOS
    sos_res = client.post(
        "/api/v1/emergency/sos",
        json={
            "latitude": 34.052235,
            "longitude": -118.243683,
            "message": "Testing emergency resolution flow",
        },
        headers=test_user["headers"],
    )
    assert sos_res.status_code == 200
    alert_id = sos_res.json()["alert_id"]

    # 2. Get alert detail
    detail_res = client.get(f"/api/v1/emergency/sos/{alert_id}", headers=test_user["headers"])
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["alert_id"] == alert_id
    assert detail["status"] == "dispatched"
    assert detail["latitude"] == 34.052235

    # 3. Resolve alert
    resolve_res = client.post(
        f"/api/v1/emergency/sos/{alert_id}/resolve",
        json={"resolution_notes": "Safe at home. Verified by escort."},
        headers=test_user["headers"],
    )
    assert resolve_res.status_code == 200
    resolve_data = resolve_res.json()
    assert resolve_data["status"] == "resolved"
    assert resolve_data["alert_id"] == alert_id
    assert resolve_data["resolution_notes"] == "Safe at home. Verified by escort."

    # 4. Verify updated detail reflects resolved state
    updated_detail = client.get(f"/api/v1/emergency/sos/{alert_id}", headers=test_user["headers"]).json()
    assert updated_detail["status"] == "resolved"
    assert updated_detail["resolved_at"] is not None
    assert updated_detail["resolution_notes"] == "Safe at home. Verified by escort."


def test_sos_with_client_provided_loved_ones(client):
    """Verify SOS dispatch prioritizes client-provided loved ones (Mom) and nearest police."""
    payload = {
        "latitude": 22.78133,
        "longitude": 86.14401,
        "message": "Immediate help needed",
        "client_contacts": [
            {
                "name": "Mom",
                "phone": "+91-9876543210",
                "relationship": "Mother",
            }
        ],
    }
    res = client.post("/api/v1/emergency/sos", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert len(data["contacts"]) == 1
    assert data["contacts"][0]["name"] == "Mom"
    assert data["contacts"][0]["phone"] == "+91-9876543210"
    assert "nearest_police" in data
    assert data["nearest_police"] is not None
    assert "name" in data["nearest_police"]
    assert "distance_text" in data["nearest_police"]
    assert data["nearest_police"]["emergency_dial"] == "112"

