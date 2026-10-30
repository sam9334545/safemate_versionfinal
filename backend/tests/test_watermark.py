"""Unit tests for the Forensic Leak Tracer invisible watermarking service."""

import io
import zipfile
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import datetime
from app.main import app
from app.database import SessionLocal
from app.models import UserModel
from app.utils.auth import create_access_token


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_headers():
    email = "test_watermark_user@safemate.org"
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
    token = create_access_token(data={"sub": email, "user_id": user.id})
    return {"Authorization": f"Bearer {token}"}


def _create_sample_image(width: int = 256, height: int = 256, color=(100, 140, 180)) -> bytes:
    """Helper creating an in-memory sample PNG image."""
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_watermark_routes_in_openapi(client):
    """Verify all Leak Tracer endpoints are registered in OpenAPI spec."""
    openapi = app.openapi()
    paths = openapi["paths"]
    assert "/api/v1/watermark/embed" in paths
    assert "/api/v1/watermark/batch-embed" in paths
    assert "/api/v1/watermark/extract" in paths
    assert "/api/v1/watermark/health" in paths


def test_embed_single_watermark_success(client, auth_headers):
    """Verify embedding per-recipient watermark returns valid image and headers."""
    sample_bytes = _create_sample_image()
    files = {"file": ("test.png", sample_bytes, "image/png")}
    data = {
        "recipient_name": "Liam Walker",
        "owner_id": "usr_maya_lin",
        "notes": "Strictly confidential - do not distribute",
    }

    response = client.post("/api/v1/watermark/embed", files=files, data=data, headers=auth_headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-watermark-recipient"] == "Liam Walker"
    assert "x-watermark-id" in response.headers
    assert "x-watermark-fingerprint" in response.headers
    assert len(response.content) > 100


def test_extract_watermark_identifies_leaker(client, auth_headers):
    """Verify extraction correctly attributes leaked media to the recipient."""
    sample_bytes = _create_sample_image()
    # 1. Embed watermark for Jake Miller
    embed_res = client.post(
        "/api/v1/watermark/embed",
        files={"file": ("photo.png", sample_bytes, "image/png")},
        data={"recipient_name": "Jake Miller", "owner_id": "usr_maya"},
        headers=auth_headers,
    )
    assert embed_res.status_code == 200
    watermarked_bytes = embed_res.content

    # 2. Inspect leaked copy
    extract_res = client.post(
        "/api/v1/watermark/extract",
        files={"file": ("leaked_leak.png", watermarked_bytes, "image/png")},
    )
    assert extract_res.status_code == 200

    report = extract_res.json()
    assert report["detected"] is True
    assert report["status"] == "match_found"
    assert report["recipient_name"] == "Jake Miller"
    assert report["confidence_score"] == 1.0
    assert report["tamper_detected"] is False
    assert "Jake Miller" in report["audit_report"]["evidence_affidavit"]
    assert len(report["audit_report"]["actionable_remedies"]) >= 2


def test_extract_clean_unwatermarked_image(client):
    """Verify extracting from a clean image returns detected=False."""
    sample_bytes = _create_sample_image()
    response = client.post(
        "/api/v1/watermark/extract",
        files={"file": ("clean.png", sample_bytes, "image/png")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["detected"] is False
    assert data["status"] == "no_watermark_detected"
    assert data["recipient_name"] is None


def test_batch_embed_bundle_zip(client, auth_headers):
    """Verify batch watermark generator creates individual copies for each recipient."""
    sample_bytes = _create_sample_image()
    files = {"file": ("group_photo.png", sample_bytes, "image/png")}
    data = {
        "recipients": "Alice Green, Bob Ross, Charlie Brown",
        "owner_id": "usr_sarah",
    }

    response = client.post("/api/v1/watermark/batch-embed", files=files, data=data, headers=auth_headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["x-recipient-count"] == "3"

    # Verify ZIP contents
    zip_bytes = io.BytesIO(response.content)
    with zipfile.ZipFile(zip_bytes, "r") as zf:
        namelist = zf.namelist()
        assert "protected_copy_Alice_Green.png" in namelist
        assert "protected_copy_Bob_Ross.png" in namelist
        assert "protected_copy_Charlie_Brown.png" in namelist
        assert "FORENSIC_DISTRIBUTION_MANIFEST.json" in namelist

        # Extract Alice's copy and verify it traces specifically to Alice
        alice_bytes = zf.read("protected_copy_Alice_Green.png")
        extract_res = client.post(
            "/api/v1/watermark/extract",
            files={"file": ("alice_copy.png", alice_bytes, "image/png")},
        )
        assert extract_res.status_code == 200
        assert extract_res.json()["recipient_name"] == "Alice Green"


def test_watermark_health(client):
    """Verify watermark health probe."""
    response = client.get("/api/v1/watermark/health")
    assert response.status_code == 200
    assert response.json()["service"] == "forensic_leak_tracer"


def test_embed_stealth_mode_without_visible_stamp(client, auth_headers):
    """Verify stealth mode (visible_stamp='false') embeds invisible watermark and extracts accurately."""
    sample_bytes = _create_sample_image(300, 300)
    files = {"file": ("stealth.png", sample_bytes, "image/png")}
    data = {
        "recipient_name": "Stealth Recipient",
        "visible_stamp": "false",
    }
    res = client.post("/api/v1/watermark/embed", files=files, data=data, headers=auth_headers)
    assert res.status_code == 200
    assert res.headers["x-watermark-recipient"] == "Stealth Recipient"

    # Extract back
    extract_res = client.post(
        "/api/v1/watermark/extract",
        files={"file": ("stealth_leak.png", res.content, "image/png")},
    )
    assert extract_res.status_code == 200
    report = extract_res.json()
    assert report["detected"] is True
    assert report["recipient_name"] == "Stealth Recipient"
    assert report["confidence_score"] == 1.0


def test_cors_watermark_headers_exposed(client, auth_headers):
    """Verify CORS preflight / headers expose watermark tracking fields."""
    sample_bytes = _create_sample_image()
    res = client.post(
        "/api/v1/watermark/embed",
        files={"file": ("test.png", sample_bytes, "image/png")},
        data={"recipient_name": "CORS Check"},
        headers={"Origin": "http://localhost:5173", **auth_headers},
    )
    assert res.status_code == 200
    exposed = res.headers.get("access-control-expose-headers", "")
    assert "x-watermark-fingerprint" in exposed.lower()
    assert "x-watermark-id" in exposed.lower()
    assert "content-disposition" in exposed.lower()
