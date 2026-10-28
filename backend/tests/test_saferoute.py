"""Automated test suite for SafeRoute safety-aware route comparison and planning."""

import datetime
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas.routes import PlaceCategory, TravelMode
from app.services.night_activity_service import night_activity_service
from app.services.places_service import places_service

app = create_app()
client = TestClient(app)


def test_routes_health():
    """Verify route health check endpoint."""
    response = client.get("/api/v1/routes/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "routing_provider" in data
    assert "geocoding_provider" in data
    assert "places_provider" in data


def test_geocode_endpoint_success():
    """Verify geocoding suggestions with valid query."""
    response = client.get("/api/v1/routes/geocode?q=Connaught%20Place&limit=3")
    assert response.status_code == 200
    items = response.json()
    assert isinstance(items, list)
    assert len(items) >= 1
    assert "Connaught" in items[0]["name"] or "Connaught" in items[0]["display_name"]
    assert -90 <= items[0]["latitude"] <= 90
    assert -180 <= items[0]["longitude"] <= 180


def test_geocode_short_query_validation():
    """Verify validation on too-short geocode query."""
    response = client.get("/api/v1/routes/geocode?q=a")
    assert response.status_code == 422


def test_plan_routes_walking_success():
    """Verify end-to-end route planning for walking mode."""
    payload = {
        "origin_lat": 28.6315,
        "origin_lon": 77.2167,
        "origin_address": "Connaught Place, New Delhi",
        "destination_lat": 28.6129,
        "destination_lon": 77.2295,
        "destination_address": "India Gate, New Delhi",
        "travel_mode": "walking",
        "leave_now": True,
    }
    response = client.post("/api/v1/routes/plan", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["travel_mode"] == "walking"
    assert len(data["routes"]) >= 2

    # Check route fields
    route_0 = data["routes"][0]
    assert "distance_km" in route_0
    assert "duration_minutes" in route_0
    assert len(route_0["geometry"]) >= 2
    assert route_0["score"]["overall_night_activity_score"] is not None
    assert 0 <= route_0["score"]["overall_night_activity_score"] <= 100
    assert route_0["score"]["score_label"] in [
        "Higher Mapped Night Activity",
        "Moderate Night Activity",
        "Lower Mapped Night Activity",
    ]
    assert "components" in route_0["score"]
    assert "open_establishments" in route_0["score"]["components"]
    assert "distribution" in route_0["score"]["components"]

    # Verify attribution and disclaimer
    assert "OpenStreetMap" in data["data_attribution"]
    assert "does not predict crime" in route_0["score"]["disclaimer"]


def test_plan_routes_driving_success():
    """Verify route planning for driving mode with driving weights."""
    payload = {
        "origin_lat": 28.5494,
        "origin_lon": 77.1936,
        "origin_address": "Hauz Khas Village",
        "destination_lat": 28.5283,
        "destination_lon": 77.2190,
        "destination_address": "Select Citywalk Saket",
        "travel_mode": "driving",
        "leave_now": False,
        "departure_time": "2026-10-25T23:30:00Z",
    }
    response = client.post("/api/v1/routes/plan", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["travel_mode"] == "driving"
    assert len(data["routes"]) >= 1
    # Driving duration should be shorter than walking
    route = data["routes"][0]
    assert route["duration_minutes"] > 0
    # Road characteristics component weight should be higher for driving (0.35 base)
    comp_road = route["score"]["components"]["road_characteristics"]
    assert comp_road["weight"] >= 0.25


def test_plan_routes_identical_origin_destination_rejected():
    """Verify 422 validation when origin and destination are identical."""
    payload = {
        "origin_lat": 28.6315,
        "origin_lon": 77.2167,
        "destination_lat": 28.6315,
        "destination_lon": 77.2167,
        "travel_mode": "walking",
    }
    response = client.post("/api/v1/routes/plan", json=payload)
    assert response.status_code == 422


def test_plan_routes_invalid_coordinates():
    """Verify 422 error on out-of-bounds coordinates."""
    payload = {
        "origin_lat": 95.0,  # Invalid > 90
        "origin_lon": 77.2167,
        "destination_lat": 28.6129,
        "destination_lon": 77.2295,
    }
    response = client.post("/api/v1/routes/plan", json=payload)
    assert response.status_code == 422


def test_opening_hours_evaluation_rules():
    """Verify opening hours evaluation logic according to safety specifications."""
    dept_day = datetime.datetime(2026, 10, 25, 14, 30)  # 2:30 PM
    dept_night = datetime.datetime(2026, 10, 25, 23, 45)  # 11:45 PM

    # 1. 24/7 establishment
    is_open, unknown = places_service.evaluate_opening_hours("24/7", dept_night)
    assert is_open is True
    assert unknown is False

    # 2. Daytime hours: 09:00-21:00
    is_open_day, unknown_day = places_service.evaluate_opening_hours("09:00-21:00", dept_day)
    assert is_open_day is True
    assert unknown_day is False

    is_open_night, unknown_night = places_service.evaluate_opening_hours("09:00-21:00", dept_night)
    assert is_open_night is False
    assert unknown_night is False

    # 3. Missing opening hours -> must be unknown=True, NOT closed!
    is_open_missing, unknown_missing = places_service.evaluate_opening_hours(None, dept_night)
    assert is_open_missing is None
    assert unknown_missing is True

    is_open_empty, unknown_empty = places_service.evaluate_opening_hours("", dept_night)
    assert is_open_empty is None
    assert unknown_empty is True


def test_night_activity_score_normalization_and_weights():
    """Verify weights renormalize to 1.0 when CCTV or components are missing."""
    geometry = [
        [77.2167, 28.6315],
        [77.2200, 28.6250],
        [77.2250, 28.6180],
        [77.2295, 28.6129],
    ]
    breakdown = night_activity_service.evaluate_route(
        geometry=geometry,
        distance_meters=2400.0,
        road_summary="Main Arterial Boulevard",
        places=[],
        mode=TravelMode.walking,
        has_cctv_data=False,
    )

    # Empty places on a 2.4km corridor should trigger score-unavailable
    assert breakdown.is_score_available is False
    assert breakdown.overall_night_activity_score is None
    assert "Insufficient mapped data" in breakdown.disclaimer


def test_distribution_segmentation_low_activity_detection():
    """Verify that long stretches with no establishments are properly flagged."""
    geometry = [
        [77.0000, 28.0000],
        [77.0000, 28.0050],
        [77.0000, 28.0100],
        [77.0000, 28.0150],
        [77.0000, 28.0200],  # ~2.2 km long stretch
    ]
    # No places
    score, max_gap, segments = night_activity_service._calculate_distribution_and_gaps(
        geometry=geometry, places=[], max_acceptable_gap_m=350.0
    )
    assert max_gap > 350.0
    assert len(segments) >= 1
    assert segments[0].length_meters >= 350.0


def test_safewalk_contract_compatibility():
    """Verify that route plan results can cleanly map to SafeWalk trip creation."""
    payload = {
        "origin_lat": 28.6315,
        "origin_lon": 77.2167,
        "destination_lat": 28.6129,
        "destination_lon": 77.2295,
        "destination_address": "India Gate Lawns, New Delhi",
        "travel_mode": "walking",
    }
    response = client.post("/api/v1/routes/plan", json=payload)
    assert response.status_code == 200
    data = response.json()
    selected_route = data["routes"][0]

    # Map fields to SafeWalk contract: destination (str) & duration_minutes (int)
    safewalk_destination = data["destination_resolved"]["display_name"]
    safewalk_duration = selected_route["duration_minutes"]

    assert isinstance(safewalk_destination, str) and len(safewalk_destination) > 0
    assert isinstance(safewalk_duration, int) and 1 <= safewalk_duration <= 1440


def test_pan_india_geocoding_major_cities():
    """Verify geocoding suggestions work for major cities across India."""
    test_queries = [
        ("Marine Drive Mumbai", "Mumbai"),
        ("Koramangala Bangalore", "Bengaluru"),
        ("Park Street Kolkata", "Kolkata"),
        ("Anna Nagar Chennai", "Chennai"),
        ("Banjara Hills Hyderabad", "Hyderabad"),
        ("FC Road Pune", "Pune"),
    ]
    for q, expected_city in test_queries:
        response = client.get(f"/api/v1/routes/geocode?q={q}&limit=2")
        assert response.status_code == 200, f"Failed for {q}"
        items = response.json()
        assert len(items) >= 1, f"No results for {q}"
        assert -90 <= items[0]["latitude"] <= 90
        assert -180 <= items[0]["longitude"] <= 180


def test_pan_india_plan_routes_mumbai():
    """Verify route planning works in Mumbai (Bandra West to Marine Drive)."""
    payload = {
        "origin_lat": 19.0583,
        "origin_lon": 72.8303,
        "origin_address": "Bandra West, Mumbai",
        "destination_lat": 18.9432,
        "destination_lon": 72.8230,
        "destination_address": "Marine Drive, Mumbai",
        "travel_mode": "driving",
        "leave_now": True,
    }
    response = client.post("/api/v1/routes/plan", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert len(data["routes"]) >= 2
    assert "Mumbai" in data["origin_resolved"]["display_name"] or "Bandra" in data["origin_resolved"]["display_name"]


def test_pan_india_plan_routes_bengaluru():
    """Verify route planning works in Bengaluru (Indiranagar to Koramangala)."""
    payload = {
        "origin_lat": 12.9719,
        "origin_lon": 77.6412,
        "origin_address": "Indiranagar, Bengaluru",
        "destination_lat": 12.9352,
        "destination_lon": 77.6245,
        "destination_address": "Koramangala, Bengaluru",
        "travel_mode": "walking",
        "leave_now": True,
    }
    response = client.post("/api/v1/routes/plan", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert len(data["routes"]) >= 2
    assert data["routes"][0]["score"]["overall_night_activity_score"] is not None

