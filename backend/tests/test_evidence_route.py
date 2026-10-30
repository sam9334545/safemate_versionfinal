"""Unit and integration tests for the Disguised Evidence Vault route."""

import datetime
import hashlib
import io
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.routes.evidence import SECURE_STORAGE_DIR
from app.database import SessionLocal
from app.models import EvidenceRecord, UserModel
from app.utils.auth import create_access_token


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Fixture ensuring an authenticated user exists and returns Bearer token headers."""
    email = "test_evidence_user@safemate.org"
    with SessionLocal() as db:
        user = db.query(UserModel).filter(UserModel.email == email).first()
        if not user:
            user = UserModel(
                email=email,
                hashed_password="hashed_test_password",
                created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                is_active=True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
    token = create_access_token(data={"sub": email, "user_id": user.id})
    return {"Authorization": f"Bearer {token}"}


def test_evidence_route_in_openapi(client):
    """Verify POST /api/v1/evidence/upload is registered under tag 'Disguised Evidence Vault'."""
    openapi = app.openapi()
    paths = openapi["paths"]
    assert "/api/v1/evidence/upload" in paths
    post_op = paths["/api/v1/evidence/upload"]["post"]
    assert "Disguised Evidence Vault" in post_op["tags"]


def test_upload_evidence_photo_success(client, auth_headers):
    """Test successful multipart upload of image evidence with coordinates and note."""
    file_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRmock_photo_bytes_12345"
    expected_sha256 = hashlib.sha256(file_bytes).hexdigest()

    form_data = {
        "user_id": "usr_safemate_001",
        "latitude": "37.774929",
        "longitude": "-122.419416",
        "note": "Suspicious activity recorded near campus perimeter.",
    }
    files = {
        "file": ("incident_photo.png", io.BytesIO(file_bytes), "image/png"),
    }

    response = client.post("/api/v1/evidence/upload", data=form_data, files=files, headers=auth_headers)
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "success"
    assert data["file_name"] == "incident_photo.png"
    assert data["file_size"] == len(file_bytes)
    assert data["sha256_checksum"] == expected_sha256
    assert data["sha256_hash"] == expected_sha256
    assert "timestamp" in data
    assert "simulated_dispatch_status" in data
    assert "CONFIRMED" in data["simulated_dispatch_status"]
    assert data["user_id"] == "usr_safemate_001"
    assert data["latitude"] == 37.774929
    assert data["longitude"] == -122.419416
    assert data["note"] == "Suspicious activity recorded near campus perimeter."
    assert "https://maps.google.com/?q=37.774929,-122.419416" in data["google_maps_url"]

    # Verify file is stored in secure private directory
    evidence_id = data["evidence_id"]
    stored_file = SECURE_STORAGE_DIR / f"{evidence_id}_incident_photo.png"
    assert stored_file.exists()
    assert stored_file.read_bytes() == file_bytes

    # Clean up test artifact
    if stored_file.exists():
        stored_file.unlink()


def test_upload_evidence_unauthenticated_returns_401(client):
    """Test uploading evidence without Bearer token returns 401 Unauthorized."""
    files = {
        "file": ("incident.png", io.BytesIO(b"data"), "image/png"),
    }
    form_data = {
        "user_id": "usr_unauth",
        "latitude": "37.774929",
        "longitude": "-122.419416",
    }
    response = client.post("/api/v1/evidence/upload", data=form_data, files=files)
    assert response.status_code == 401


def test_upload_evidence_audio_without_note(client, auth_headers):
    """Test successful multipart upload of audio recording without optional note."""
    audio_bytes = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00mock_audio_stream"
    expected_sha256 = hashlib.sha256(audio_bytes).hexdigest()

    form_data = {
        "user_id": "usr_safemate_888",
        "latitude": "40.712776",
        "longitude": "-74.005974",
    }
    files = {
        "file": ("disguised_memo.wav", io.BytesIO(audio_bytes), "audio/wav"),
    }

    response = client.post("/api/v1/evidence/upload", data=form_data, files=files, headers=auth_headers)
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "success"
    assert data["file_name"] == "disguised_memo.wav"
    assert data["file_size"] == len(audio_bytes)
    assert data["sha256_checksum"] == expected_sha256
    assert data["note"] is None
    assert any("Maya Lin" in c for c in data["contacts_alerted"])

    # Clean up test artifact
    stored_audio = SECURE_STORAGE_DIR / f"{data['evidence_id']}_disguised_memo.wav"
    if stored_audio.exists():
        stored_audio.unlink()


def test_upload_empty_file_returns_400(client, auth_headers):
    """Test uploading an empty binary file raises HTTP 400."""
    form_data = {
        "user_id": "usr_safemate_001",
        "latitude": "37.774929",
        "longitude": "-122.419416",
    }
    files = {
        "file": ("empty.png", io.BytesIO(b""), "image/png"),
    }

    response = client.post("/api/v1/evidence/upload", data=form_data, files=files, headers=auth_headers)
    assert response.status_code == 400
    assert "empty" in response.json().get("detail", "").lower()


def test_upload_invalid_coordinates_returns_422(client, auth_headers):
    """Test invalid latitude coordinate produces validation error (HTTP 422)."""
    files = {
        "file": ("sample.png", io.BytesIO(b"dummy_content"), "image/png"),
    }
    form_data = {
        "user_id": "usr_safemate_001",
        "latitude": "120.0",  # Out of range [-90, 90]
        "longitude": "-122.419416",
    }

    response = client.post("/api/v1/evidence/upload", data=form_data, files=files, headers=auth_headers)
    assert response.status_code == 422


def test_upload_missing_user_id_returns_422(client, auth_headers):
    """Test omitted user_id returns validation error (HTTP 422)."""
    files = {
        "file": ("sample.png", io.BytesIO(b"dummy_content"), "image/png"),
    }
    form_data = {
        "latitude": "37.774929",
        "longitude": "-122.419416",
    }

    response = client.post("/api/v1/evidence/upload", data=form_data, files=files, headers=auth_headers)
    assert response.status_code == 422


def test_root_endpoint_lists_evidence_upload(client):
    """Verify root endpoint discovery lists the evidence upload endpoint."""
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["evidence_upload"] == "/api/v1/evidence/upload"
    assert response.json()["evidence_download"] == "/api/v1/evidence/download/{evidence_id}"


def test_download_evidence_success(client, auth_headers):
    """Test downloading a previously uploaded evidence binary."""
    content = b"evidence_binary_content_for_download_test"
    files = {"file": ("recording.wav", io.BytesIO(content), "audio/wav")}
    form_data = {
        "user_id": "usr_safemate_001",
        "latitude": "37.774929",
        "longitude": "-122.419416",
        "note": "Download test audio",
    }
    upload_res = client.post("/api/v1/evidence/upload", data=form_data, files=files, headers=auth_headers)
    assert upload_res.status_code == 200
    evidence_id = upload_res.json()["evidence_id"]

    download_res = client.get(f"/api/v1/evidence/download/{evidence_id}", headers=auth_headers)
    assert download_res.status_code == 200
    assert download_res.content == content


def test_download_evidence_not_found(client, auth_headers):
    """Test downloading non-existent evidence returns 404."""
    download_res = client.get("/api/v1/evidence/download/evd_nonexistent999", headers=auth_headers)
    assert download_res.status_code == 404


def test_get_and_delete_evidence_record(client, auth_headers):
    """Test retrieving single evidence metadata and securely deleting it."""
    content = b"evidence_binary_for_deletion_test"
    files = {"file": ("temp_evidence.wav", io.BytesIO(content), "audio/wav")}
    form_data = {
        "user_id": "usr_safemate_001",
        "latitude": "37.774929",
        "longitude": "-122.419416",
        "note": "Temporary test audio for deletion",
    }
    upload_res = client.post("/api/v1/evidence/upload", data=form_data, files=files, headers=auth_headers)
    assert upload_res.status_code == 200
    evidence_id = upload_res.json()["evidence_id"]

    # 1. Get single evidence record
    get_res = client.get(f"/api/v1/evidence/record/{evidence_id}", headers=auth_headers)
    assert get_res.status_code == 200
    record = get_res.json()
    assert record["evidence_id"] == evidence_id
    assert record["file_name"] == "temp_evidence.wav"

    # 2. Delete evidence record
    del_res = client.delete(f"/api/v1/evidence/record/{evidence_id}", headers=auth_headers)
    assert del_res.status_code == 200
    del_data = del_res.json()
    assert del_data["status"] == "success"
    assert del_data["evidence_id"] == evidence_id

    # 3. Verify get returns 404 after deletion
    get_after = client.get(f"/api/v1/evidence/record/{evidence_id}", headers=auth_headers)
    assert get_after.status_code == 404




def test_download_evidence_unauthenticated_returns_401(client, auth_headers):
    """Verify downloading evidence without auth token returns 401 Unauthorized."""
    content = b"evidence_binary_unauth_test"
    files = {"file": ("unauth_test.wav", io.BytesIO(content), "audio/wav")}
    form_data = {
        "user_id": "usr_safemate_001",
        "latitude": "37.774929",
        "longitude": "-122.419416",
        "note": "Unauth download test",
    }
    upload_res = client.post("/api/v1/evidence/upload", data=form_data, files=files, headers=auth_headers)
    assert upload_res.status_code == 200
    evidence_id = upload_res.json()["evidence_id"]

    # Attempt download without Authorization header or token query param
    download_res = client.get(f"/api/v1/evidence/download/{evidence_id}")
    assert download_res.status_code == 401
    assert "authentication required" in download_res.json()["detail"].lower()


def test_export_evidence_authenticated_injects_credentials(client, auth_headers):
    """Verify /export endpoint strictly authenticates and injects user credentials."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color="red").save(buf, format="PNG")
    content = buf.getvalue()
    files = {"file": ("sealed_photo.png", io.BytesIO(content), "image/png")}
    form_data = {
        "user_id": "usr_safemate_001",
        "latitude": "37.774929",
        "longitude": "-122.419416",
        "note": "Sealed export test photo",
    }
    upload_res = client.post("/api/v1/evidence/upload", data=form_data, files=files, headers=auth_headers)
    assert upload_res.status_code == 200
    evidence_id = upload_res.json()["evidence_id"]

    # 1. Unauthenticated export returns 401
    unauth_res = client.get(f"/api/v1/evidence/export/{evidence_id}")
    assert unauth_res.status_code == 401

    # 2. Authenticated export succeeds and injects user credentials into headers
    auth_export_res = client.get(f"/api/v1/evidence/export/{evidence_id}", headers=auth_headers)
    assert auth_export_res.status_code == 200
    assert "X-Sealed-By" in auth_export_res.headers
    assert "test_evidence_user@safemate.org" in auth_export_res.headers["X-Sealed-By"]
    assert "X-Seal-Timestamp" in auth_export_res.headers
    assert "X-Verification-Hash" in auth_export_res.headers
    assert auth_export_res.headers["X-Chain-Of-Custody"] == "VERIFIED_AUTHENTIC"


def test_get_evidence_certificate_authenticated(client, auth_headers):
    """Verify Section 63 BSA certificate generation endpoint."""
    content = b"evidence_for_cert_test"
    files = {"file": ("cert_test.m4a", io.BytesIO(content), "audio/mp4")}
    form_data = {
        "user_id": "usr_safemate_001",
        "latitude": "28.6139",
        "longitude": "77.2090",
        "note": "Legal certification test record",
    }
    upload_res = client.post("/api/v1/evidence/upload", data=form_data, files=files, headers=auth_headers)
    assert upload_res.status_code == 200
    evidence_id = upload_res.json()["evidence_id"]

    # 1. Unauthenticated returns 401
    unauth_cert = client.get(f"/api/v1/evidence/certificate/{evidence_id}")
    assert unauth_cert.status_code == 401

    # 2. Authenticated returns certified Section 63 BSA record
    cert_res = client.get(f"/api/v1/evidence/certificate/{evidence_id}", headers=auth_headers)
    assert cert_res.status_code == 200
    cert = cert_res.json()
    assert "CERT-BSA63-" in cert["certificate_id"]
    assert cert["certified_by"] == "test_evidence_user@safemate.org"
    assert cert["evidence_id"] == evidence_id
    assert cert["sha256_checksum"] == upload_res.json()["sha256_checksum"]
    assert cert["integrity_status"] == "VERIFIED_TAMPER_EVIDENT_UNALTERED"
    assert len(cert["verification_hash"]) == 64


def test_seal_evidence_record_endpoint(client, auth_headers):
    """Verify /seal endpoint creates cryptographic seal metadata."""
    content = b"evidence_for_seal_endpoint"
    files = {"file": ("seal_test.wav", io.BytesIO(content), "audio/wav")}
    form_data = {
        "user_id": "usr_safemate_001",
        "latitude": "28.6139",
        "longitude": "77.2090",
        "note": "Seal test record",
    }
    upload_res = client.post("/api/v1/evidence/upload", data=form_data, files=files, headers=auth_headers)
    assert upload_res.status_code == 200
    evidence_id = upload_res.json()["evidence_id"]

    seal_res = client.post(f"/api/v1/evidence/seal/{evidence_id}", headers=auth_headers)
    assert seal_res.status_code == 200
    seal_data = seal_res.json()
    assert seal_data["status"] == "sealed"
    assert seal_data["sealed_by"] == "test_evidence_user@safemate.org"
    assert len(seal_data["verification_hash"]) == 64
