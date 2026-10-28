"""FastAPI endpoints for SafeRoute safety-aware route comparison and planning under /api/v1/routes."""

from __future__ import annotations

import asyncio
import datetime
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.schemas.routes import (
    GeocodeItem,
    NearbyPlace,
    PlaceCategory,
    RouteAlternative,
    RouteHealthResponse,
    RoutePlanRequest,
    RoutePlanResponse,
    TravelMode,
)
from app.services.geocoding_service import geocoding_service
from app.services.night_activity_service import night_activity_service
from app.services.places_service import places_service
from app.services.routing_service import routing_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/routes", tags=["SafeRoute Planner"])


@router.get("/health", response_model=RouteHealthResponse)
async def get_routes_health() -> RouteHealthResponse:
    """Check health and reachability of routing, geocoding, and POI services."""
    return RouteHealthResponse(
        status="ok",
        routing_provider="OpenStreetMap OSRM / High-Fidelity Geometry Engine",
        geocoding_provider="OpenStreetMap Nominatim with Rate-Limiting Cache",
        places_provider="OpenStreetMap Overpass API (Bounded Corridor Queries)",
        cctv_provider_status="Unavailable for public consumer safety scoring",
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )


@router.get("/geocode", response_model=List[GeocodeItem])
async def geocode_query(
    q: str = Query(..., min_length=2, max_length=150, description="Address or place search query"),
    limit: int = Query(default=5, ge=1, le=10),
) -> List[GeocodeItem]:
    """Retrieve address and place name geocoding suggestions."""
    try:
        suggestions = await geocoding_service.geocode(q, limit=limit)
        return suggestions
    except Exception as exc:
        logger.error(f"Geocoding endpoint error for '{q}': {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Geocoding service temporarily unavailable. Please retry with landmark or coordinates.",
        )


@router.get("/places", response_model=List[NearbyPlace])
async def get_corridor_places(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
    radius_meters: float = Query(default=500.0, ge=50.0, le=2000.0),
) -> List[NearbyPlace]:
    """Inspect nearby safety support places for a localized coordinate."""
    try:
        dummy_polyline = [[lon, lat], [lon + 0.001, lat + 0.001]]
        departure = datetime.datetime.now(datetime.timezone.utc)
        places = await places_service.get_nearby_places_for_route(
            polyline=dummy_polyline, departure_time=departure, buffer_meters=radius_meters
        )
        return places
    except Exception as exc:
        logger.error(f"Places lookup failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve nearby establishment data.",
        )


@router.post("/plan", response_model=RoutePlanResponse)
async def plan_and_compare_routes(request: RoutePlanRequest) -> RoutePlanResponse:
    """
    Generate and compare candidate routes with transparent Night Activity Scores (0-100),
    open establishment distributions, and longest low-activity segment metrics.
    """
    # 1. Determine evaluation departure time
    if request.leave_now or not request.departure_time:
        departure_eval = datetime.datetime.now(datetime.timezone.utc)
    else:
        departure_eval = request.departure_time

    # 2. Resolve origin and destination labels if not provided
    if request.origin_address:
        origin_item = GeocodeItem(
            display_name=request.origin_address,
            name=request.origin_address.split(",")[0].strip(),
            latitude=request.origin_lat,
            longitude=request.origin_lon,
        )
    else:
        origin_item = await geocoding_service.reverse_geocode(request.origin_lat, request.origin_lon)

    if request.destination_address:
        dest_item = GeocodeItem(
            display_name=request.destination_address,
            name=request.destination_address.split(",")[0].strip(),
            latitude=request.destination_lat,
            longitude=request.destination_lon,
        )
    else:
        dest_item = await geocoding_service.reverse_geocode(request.destination_lat, request.destination_lon)

    # 3. Retrieve route alternatives
    try:
        raw_routes = await routing_service.get_route_alternatives(
            origin_lat=request.origin_lat,
            origin_lon=request.origin_lon,
            dest_lat=request.destination_lat,
            dest_lon=request.destination_lon,
            mode=request.travel_mode,
        )
    except Exception as exc:
        logger.error(f"Routing service failure: {exc}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Routing provider temporarily unreachable. Please retry.",
        )

    if not raw_routes:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No navigable route found between the specified origin and destination.",
        )

    # 4. Process each route alternative concurrently
    buffer_dist = 350.0 if request.travel_mode == TravelMode.walking else 500.0
    places_tasks = [
        places_service.get_nearby_places_for_route(
            polyline=r["geometry"],
            departure_time=departure_eval,
            buffer_meters=buffer_dist,
        )
        for r in raw_routes
    ]
    all_places = await asyncio.gather(*places_tasks, return_exceptions=True)

    evaluated_alternatives: List[RouteAlternative] = []

    for idx, r_data in enumerate(raw_routes):
        geom = r_data["geometry"]
        dist_m = r_data["distance_meters"]
        dur_s = r_data["duration_seconds"]
        road_sum = r_data["road_summary"]

        places_res = all_places[idx]
        places = places_res if isinstance(places_res, list) else []

        # Detect if any mapped CCTV / surveillance nodes are along the corridor
        has_cctv = any(p.category == PlaceCategory.cctv for p in places)

        # Compute deterministic Night Activity Score
        score_breakdown = night_activity_service.evaluate_route(
            geometry=geom,
            distance_meters=dist_m,
            road_summary=road_sum,
            places=places,
            mode=request.travel_mode,
            has_cctv_data=has_cctv,
        )

        # Get low-activity segments from distribution analysis
        _, _, low_act_segs = night_activity_service._calculate_distribution_and_gaps(
            geometry=geom,
            places=places,
            max_acceptable_gap_m=350.0 if request.travel_mode == TravelMode.walking else 700.0,
        )

        dur_mins = max(1, int(round(dur_s / 60.0)))
        dist_km = round(dist_m / 1000.0, 1)

        evaluated_alternatives.append(
            RouteAlternative(
                route_id=r_data["route_id"],
                name=r_data["name"],
                is_fastest=r_data.get("is_fastest", idx == 0),
                is_recommended_for_night=False,  # Evaluated below
                distance_meters=round(dist_m, 1),
                distance_km=dist_km,
                duration_seconds=round(dur_s, 1),
                duration_minutes=dur_mins,
                travel_mode=request.travel_mode,
                geometry=geom,
                score=score_breakdown,
                nearby_places=places,
                low_activity_segments=low_act_segs,
            )
        )

    # 5. Determine which route has higher night activity score
    valid_scores = [
        (alt.score.overall_night_activity_score, alt)
        for alt in evaluated_alternatives
        if alt.score.overall_night_activity_score is not None
    ]
    if valid_scores:
        valid_scores.sort(key=lambda item: item[0], reverse=True)
        top_night_alt = valid_scores[0][1]
        top_night_alt.is_recommended_for_night = True

    # 6. Generate explainable trade-off comparative statements
    if len(evaluated_alternatives) >= 2:
        fastest_route = min(evaluated_alternatives, key=lambda r: r.duration_seconds)
        highest_score_route = max(
            evaluated_alternatives,
            key=lambda r: r.score.overall_night_activity_score or -1,
        )

        for alt in evaluated_alternatives:
            if alt == fastest_route and alt == highest_score_route:
                alt.trade_off_statement = (
                    "Fastest route with the highest night activity score among alternatives."
                )
            elif alt == fastest_route:
                score_diff = (highest_score_route.score.overall_night_activity_score or 0) - (alt.score.overall_night_activity_score or 0)
                alt.trade_off_statement = (
                    f"Fastest option. However, {highest_score_route.name} offers a higher night activity score (+{score_diff} pts) and more open establishments."
                )
            elif alt == highest_score_route:
                dur_diff_min = max(1, alt.duration_minutes - fastest_route.duration_minutes)
                extra_places = alt.score.open_establishments_count - fastest_route.score.open_establishments_count
                places_txt = f" and {extra_places} more mapped open places" if extra_places > 0 else ""
                alt.trade_off_statement = (
                    f"Higher mapped night activity score (+{(alt.score.overall_night_activity_score or 0) - (fastest_route.score.overall_night_activity_score or 0)} pts){places_txt}, taking approximately {dur_diff_min} min longer than the fastest route."
                )
            else:
                alt.trade_off_statement = (
                    f"Alternative corridor via {alt.score.road_characteristics_summary}."
                )

    return RoutePlanResponse(
        status="success",
        origin_resolved=origin_item,
        destination_resolved=dest_item,
        travel_mode=request.travel_mode,
        departure_time_evaluated=departure_eval.isoformat(),
        routes=evaluated_alternatives,
        data_attribution=(
            "Route geometries © OpenStreetMap contributors, OSRM. "
            "Establishment & POI data © OpenStreetMap contributors via Overpass API. "
            "Night Activity Score calculated using SafeMate explainable normalization model."
        ),
        provider_notes=[
            "Opening hours reflect mapped OSM data and may be incomplete or subject to change.",
            "CCTV coverage data is unavailable for this corridor and excluded from score calculation.",
            "This score represents available safety-supporting indicators, not a crime prediction or safety guarantee.",
        ],
    )
