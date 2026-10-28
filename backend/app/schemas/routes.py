"""Pydantic schemas and contracts for the SafeRoute Safety-Aware Route Planner."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field, field_validator, model_validator


class TravelMode(str, Enum):
    """Supported travel modes for route planning and scoring."""
    walking = "walking"
    driving = "driving"


class PlaceCategory(str, Enum):
    """Categorization for nearby safety-supporting establishments."""
    shop = "shop"
    pharmacy = "pharmacy"
    hospital = "hospital"
    police = "police"
    fuel = "fuel"
    transit = "transit"
    cctv = "cctv"


class Coordinates(BaseModel):
    """Geographic point representation."""
    latitude: float = Field(..., ge=-90.0, le=90.0, description="Latitude coordinate")
    longitude: float = Field(..., ge=-180.0, le=180.0, description="Longitude coordinate")


class GeocodeItem(BaseModel):
    """Resolved geocoding suggestion or point."""
    display_name: str = Field(..., description="Full descriptive address")
    name: str = Field(..., description="Short place or street name")
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    place_type: Optional[str] = Field(default=None, description="Type of place (e.g. amenity, street, suburb)")


class NearbyPlace(BaseModel):
    """Verified or mapped establishment near a route alternative."""
    id: str = Field(..., description="Unique place identifier")
    name: str = Field(..., description="Establishment name")
    category: PlaceCategory = Field(..., description="Safety category classification")
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    distance_to_route_meters: float = Field(..., ge=0.0, description="Perpendicular distance from route in meters")
    opening_hours_raw: Optional[str] = Field(default=None, description="Raw OSM opening_hours string if available")
    is_likely_open: Optional[bool] = Field(default=None, description="True if open at travel time, False if closed, None if unknown")
    hours_unknown: bool = Field(default=False, description="True if opening hours data was not mapped")
    details: Optional[Dict[str, Any]] = Field(default=None, description="Additional metadata or tags")


class ScoreComponent(BaseModel):
    """Transparent score breakdown component."""
    name: str = Field(..., description="Feature name")
    score: float = Field(..., ge=0.0, le=100.0, description="Component score out of 100")
    weight: float = Field(..., ge=0.0, le=1.0, description="Normalized weight applied")
    available: bool = Field(..., description="Whether empirical data was available to score this component")
    description: str = Field(..., description="Human-readable plain language explanation")


class LowActivitySegment(BaseModel):
    """Long stretch with minimal or no nearby mapped open activity."""
    start_coordinates: Coordinates
    end_coordinates: Coordinates
    length_meters: float = Field(..., ge=0.0)
    description: str = Field(..., description="Explanation of low activity stretch")


class RouteScoreBreakdown(BaseModel):
    """Explainable Night Activity Score (0-100) and indicators."""
    overall_night_activity_score: Optional[int] = Field(
        default=None,
        ge=0,
        le=100,
        description="Composite Night Activity Score from 0 to 100. None if data is insufficient."
    )
    score_label: str = Field(..., description="Label such as 'Higher Mapped Night Activity', 'Fastest Route', etc.")
    is_score_available: bool = Field(default=True, description="False if insufficient data exists to calculate score")
    components: Dict[str, ScoreComponent] = Field(default_factory=dict, description="Component scores and weights")
    open_establishments_count: int = Field(default=0, ge=0)
    unknown_hours_count: int = Field(default=0, ge=0)
    assistance_points_count: int = Field(default=0, ge=0, description="Police stations, hospitals, clinics")
    transit_points_count: int = Field(default=0, ge=0, description="Bus stops, metro stations, transit hubs")
    cctv_points_count: int = Field(default=0, ge=0, description="Surveillance cameras or campus CCTV posts")
    longest_low_activity_segment_meters: float = Field(default=0.0, ge=0.0)
    low_activity_segments_count: int = Field(default=0, ge=0)
    road_characteristics_summary: str = Field(..., description="Summary of road types used (main roads, residential, etc.)")
    disclaimer: str = Field(
        default="This score summarizes mapped activity and support indicators along the route. It does not predict crime or guarantee personal safety."
    )


class RouteAlternative(BaseModel):
    """Candidate route option with real geometry and safety indicators."""
    route_id: str = Field(..., description="Unique route alternative identifier")
    id: Optional[str] = Field(default=None, description="Alias for route_id")
    name: str = Field(..., description="Descriptive route label (e.g. 'Via MG Road', 'Via Outer Ring')")
    is_fastest: bool = Field(default=False)
    is_recommended_for_night: bool = Field(default=False)
    is_highest_activity: Optional[bool] = Field(default=None, description="Alias for is_recommended_for_night")
    distance_meters: float = Field(..., ge=0.0)
    distance_km: float = Field(..., ge=0.0)
    duration_seconds: float = Field(..., ge=0.0)
    duration_minutes: int = Field(..., ge=0)
    travel_mode: TravelMode
    geometry: Union[List[List[float]], Dict[str, Any]] = Field(
        ...,
        description="GeoJSON-standard [longitude, latitude] coordinates polyline or GeoJSON LineString"
    )
    score: RouteScoreBreakdown
    night_activity_score: Optional[int] = Field(default=None, description="Direct access to total composite score")
    score_breakdown: Optional[RouteScoreBreakdown] = Field(default=None, description="Alias for score")
    open_establishments_count: Optional[int] = Field(default=None)
    unknown_hours_count: Optional[int] = Field(default=None)
    assistance_nodes_count: Optional[int] = Field(default=None)
    cctv_nodes_count: Optional[int] = Field(default=None)
    longest_low_activity_segment_meters: Optional[float] = Field(default=None)
    road_characteristics: Optional[str] = Field(default=None)
    nearby_places: List[NearbyPlace] = Field(default_factory=list)
    low_activity_segments: List[LowActivitySegment] = Field(default_factory=list)
    trade_off_statement: Optional[str] = Field(
        default=None,
        description="Explainable comparison against other routes (e.g. 'Takes 2 mins longer with 12 more open places')"
    )

    @model_validator(mode="after")
    def populate_convenience_aliases(self) -> "RouteAlternative":
        if self.id is None:
            self.id = self.route_id
        if self.is_highest_activity is None:
            self.is_highest_activity = self.is_recommended_for_night
        if self.score_breakdown is None:
            self.score_breakdown = self.score
        if self.night_activity_score is None:
            self.night_activity_score = self.score.overall_night_activity_score
        if self.open_establishments_count is None:
            self.open_establishments_count = self.score.open_establishments_count
        if self.unknown_hours_count is None:
            self.unknown_hours_count = self.score.unknown_hours_count
        if self.assistance_nodes_count is None:
            self.assistance_nodes_count = self.score.assistance_points_count
        if self.cctv_nodes_count is None:
            self.cctv_nodes_count = self.score.cctv_points_count
        if self.longest_low_activity_segment_meters is None:
            self.longest_low_activity_segment_meters = self.score.longest_low_activity_segment_meters
        if self.road_characteristics is None:
            self.road_characteristics = self.score.road_characteristics_summary
        return self


class RoutePlanRequest(BaseModel):
    """Request payload for route planning and safety-aware comparison."""
    origin_address: Optional[str] = Field(default=None, description="Starting address or landmark name")
    origin_name: Optional[str] = Field(default=None, description="Alias for origin_address")
    origin_lat: float = Field(..., ge=-90.0, le=90.0, description="Origin latitude")
    origin_lon: float = Field(..., ge=-180.0, le=180.0, description="Origin longitude")
    destination_address: Optional[str] = Field(default=None, description="Destination address or landmark name")
    destination_name: Optional[str] = Field(default=None, description="Alias for destination_address")
    destination_lat: float = Field(..., ge=-90.0, le=90.0, description="Destination latitude")
    destination_lon: float = Field(..., ge=-180.0, le=180.0, description="Destination longitude")
    travel_mode: TravelMode = Field(default=TravelMode.walking, description="Travel mode: walking or driving")
    leave_now: bool = Field(default=True, description="True to evaluate current time")
    departure_time: Optional[datetime] = Field(default=None, description="Custom departure time if leave_now is False")

    @model_validator(mode="after")
    def validate_different_endpoints(self) -> "RoutePlanRequest":
        if self.origin_address is None and self.origin_name is not None:
            self.origin_address = self.origin_name
        if self.destination_address is None and self.destination_name is not None:
            self.destination_address = self.destination_name
        if abs(self.origin_lat - self.destination_lat) < 1e-6 and abs(self.origin_lon - self.destination_lon) < 1e-6:
            raise ValueError("Origin and destination coordinates cannot be identical.")
        return self


class RoutePlanResponse(BaseModel):
    """Response containing route alternatives, comparisons, and safety metrics."""
    status: str = Field(default="success")
    origin_resolved: GeocodeItem
    destination_resolved: GeocodeItem
    origin: Optional[GeocodeItem] = Field(default=None, description="Alias for origin_resolved")
    destination: Optional[GeocodeItem] = Field(default=None, description="Alias for destination_resolved")
    travel_mode: TravelMode
    departure_time_evaluated: str = Field(..., description="ISO 8601 timestamp evaluated for opening hours")
    routes: List[RouteAlternative] = Field(..., min_length=1, description="Candidate route alternatives")
    alternatives: Optional[List[RouteAlternative]] = Field(default=None, description="Alias for routes")
    data_attribution: str = Field(
        default="Route data © OpenStreetMap contributors, OpenRouteService / OSRM. POI data © OpenStreetMap contributors via Overpass API."
    )
    provider_notes: Optional[List[str]] = Field(default=None, description="Informational notices on data availability")

    @model_validator(mode="after")
    def populate_response_aliases(self) -> "RoutePlanResponse":
        if self.alternatives is None:
            self.alternatives = self.routes
        if self.origin is None:
            self.origin = self.origin_resolved
        if self.destination is None:
            self.destination = self.destination_resolved
        return self


class RouteHealthResponse(BaseModel):
    """Lightweight health status of the route planning service and external integrations."""
    status: str = Field(default="ok")
    routing_provider: str
    geocoding_provider: str
    places_provider: str
    cctv_provider_status: str
    timestamp: str
