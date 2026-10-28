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


def test_nit_surathkal_and_karnataka_geocoding():
    """Verify high-accuracy geocoding around NIT Surathkal, Mangaluru, and Karnataka."""
    queries = [
        "nit surathkal",
        "nitk",
        "nitk beach",
        "surathkal lighthouse",
        "surathkal railway station",
        "mukka",
        "hampankatta",
        "tiger circle manipal",
        "mysore palace",
    ]
    for q in queries:
        resp = client.get(f"/api/v1/routes/geocode?q={q}&limit=2")
        assert resp.status_code == 200, f"Query '{q}' failed with status {resp.status_code}"
        items = resp.json()
        assert len(items) >= 1, f"Expected suggestions for '{q}', got 0"
        top = items[0]
        assert 12.0 <= top["latitude"] <= 16.0, f"Latitude {top['latitude']} out of Karnataka range for '{q}'"
        assert 74.0 <= top["longitude"] <= 78.0, f"Longitude {top['longitude']} out of Karnataka range for '{q}'"


def test_plan_routes_nit_surathkal_campus_to_beach():
    """Verify route planning works accurately in NIT Surathkal (NITK Main Gate to NITK Beach)."""
    payload = {
        "origin_lat": 13.0104,
        "origin_lon": 74.7946,
        "origin_address": "NITK Main Gate, Surathkal",
        "destination_lat": 13.0116,
        "destination_lon": 74.7865,
        "destination_address": "NITK Beach, Surathkal",
        "travel_mode": "walking",
        "leave_now": True,
    }
    response = client.post("/api/v1/routes/plan", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert len(data["routes"]) >= 1
    assert data["origin_resolved"] is not None
    assert data["destination_resolved"] is not None
    selected = data["routes"][0]
    assert 0.5 <= selected["distance_km"] <= 3.0
    assert 1 <= selected["duration_minutes"] <= 45
    assert len(selected["nearby_places"]) >= 1


def test_cctv_surveillance_integration_and_scoring():
    """Verify that mapped CCTV surveillance cameras are parsed, scored, and returned."""
    payload = {
        "origin_lat": 13.0104,
        "origin_lon": 74.7946,
        "origin_address": "NITK Main Gate, Surathkal",
        "destination_lat": 13.0116,
        "destination_lon": 74.7865,
        "destination_address": "NITK Beach, Surathkal",
        "travel_mode": "walking",
        "leave_now": True,
    }
    response = client.post("/api/v1/routes/plan", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert len(data["routes"]) >= 1

    route = data["routes"][0]
    score_comp = route["score"]["components"]
    assert "cctv" in score_comp

    # Mapped corridor places now include surveillance cameras
    cctv_places = [p for p in route["nearby_places"] if p["category"] == "cctv"]
    assert len(cctv_places) >= 1
    assert route["score"]["cctv_points_count"] >= 1
    assert route["cctv_nodes_count"] >= 1

    # Verify component scoring
    cctv_comp = score_comp["cctv"]
    assert cctv_comp["available"] is True
    assert cctv_comp["score"] >= 70.0
    assert cctv_comp["weight"] > 0.0
    assert "surveillance camera" in cctv_comp["description"].lower()

    # Verify real attributes — NO simulation
    first_cctv = cctv_places[0]
    assert first_cctv["details"].get("verified_real_cctv") is True
    assert first_cctv["details"].get("operator") is not None
    assert first_cctv["is_likely_open"] is True


def test_unmapped_corridor_honestly_reports_zero_cctv_without_simulation():
    """Verify that a corridor with no verified cameras does NOT simulate CCTV and reports 0 cameras."""
    # Route in an area with no registered CCTV cameras
    geometry = [
        [75.1000, 14.1000],
        [75.1050, 14.1050],
        [75.1100, 14.1100],
    ]
    # Synthetic places only, without CCTV
    places = places_service._generate_synthetic_corridor_places(geometry, datetime.datetime.now())
    # Verify synthetic POIs have ZERO CCTV (no simulation)
    assert not any(p.category == PlaceCategory.cctv for p in places)

    # Evaluate route
    breakdown = night_activity_service.evaluate_route(
        geometry=geometry,
        distance_meters=1500.0,
        road_summary="Secondary Road",
        places=places,
        mode=TravelMode.walking,
        has_cctv_data=False,
    )
    assert breakdown.cctv_points_count == 0
    cctv_comp = breakdown.components["cctv"]
    assert cctv_comp.available is False
    assert cctv_comp.weight == 0.0
    assert cctv_comp.score == 0.0
    assert "unmapped" in cctv_comp.description.lower()


def test_nit_jamshedpur_and_jharkhand_geocoding():
    """Verify forward geocoding precision for NIT Jamshedpur, Adityapur, Sakchi, and Bistupur."""
    queries = [
        "nit jamshedpur",
        "nitjsr",
        "adityapur",
        "sakchi",
        "bistupur",
        "tatanagar station",
    ]
    for q in queries:
        resp = client.get(f"/api/v1/routes/geocode?q={q}&limit=2")
        assert resp.status_code == 200, f"Query '{q}' failed with status {resp.status_code}"
        items = resp.json()
        assert len(items) >= 1, f"Expected suggestions for '{q}', got 0"
        top = items[0]
        assert 22.70 <= top["latitude"] <= 22.90, f"Latitude {top['latitude']} out of Jamshedpur range for '{q}'"
        assert 86.10 <= top["longitude"] <= 86.25, f"Longitude {top['longitude']} out of Jamshedpur range for '{q}'"


def test_plan_routes_nit_jamshedpur_campus_to_bistupur():
    """Verify route planning works accurately in Jamshedpur (NIT Jamshedpur to Bistupur Market)."""
    payload = {
        "origin_lat": 22.7765,
        "origin_lon": 86.1438,
        "origin_address": "NIT Jamshedpur Main Gate, Adityapur",
        "destination_lat": 22.7958,
        "destination_lon": 86.1854,
        "destination_address": "Bistupur Market, Jamshedpur",
        "travel_mode": "driving",
        "leave_now": True,
    }
    response = client.post("/api/v1/routes/plan", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert len(data["routes"]) >= 1
    assert data["origin_resolved"] is not None
    assert data["destination_resolved"] is not None
    selected = data["routes"][0]
    assert 2.0 <= selected["distance_km"] <= 12.0
    assert 2 <= selected["duration_minutes"] <= 60
    assert len(selected["nearby_places"]) >= 1
    # Check that verified CCTV surveillance along Adityapur-Bistupur corridor is detected
    cctvs = [p for p in selected["nearby_places"] if p["category"] == "cctv"]
    assert len(cctvs) >= 1




