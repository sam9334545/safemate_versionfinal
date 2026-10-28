"""Night Activity Scoring Engine: deterministic, explainable safety-supporting geospatial evaluation."""

from __future__ import annotations

import logging
import math
from typing import Dict, List, Optional, Tuple

from app.schemas.routes import (
    Coordinates,
    LowActivitySegment,
    NearbyPlace,
    PlaceCategory,
    RouteScoreBreakdown,
    ScoreComponent,
    TravelMode,
)

logger = logging.getLogger(__name__)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    return r * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


class NightActivityService:
    """Computes transparent 0-100 Night Activity Scores with weight renormalization."""

    def __init__(self):
        # Default baseline weights
        self.weights_walking: Dict[str, float] = {
            "open_establishments": 0.28,
            "distribution": 0.22,
            "road_characteristics": 0.15,
            "assistance_points": 0.20,
            "public_transport": 0.15,
            "cctv": 0.10,  # Dynamically factored when mapped surveillance is present
        }

        self.weights_driving: Dict[str, float] = {
            "road_characteristics": 0.35,
            "open_establishments": 0.20,
            "distribution": 0.15,
            "assistance_points": 0.20,
            "public_transport": 0.10,
            "cctv": 0.05,
        }

    def _calculate_distribution_and_gaps(
        self,
        geometry: List[List[float]],
        places: List[NearbyPlace],
        max_acceptable_gap_m: float = 350.0,
    ) -> Tuple[float, float, List[LowActivitySegment]]:
        """
        Analyzes whether establishments are clustered or evenly distributed along the route corridor.
        Returns:
            (distribution_score_0_to_100, longest_gap_meters, list_of_low_activity_segments)
        """
        if len(geometry) < 2:
            return 50.0, 0.0, []

        # Calculate cumulative distance along polyline vertices
        cum_dist: List[float] = [0.0]
        total_dist = 0.0
        for i in range(1, len(geometry)):
            seg_len = haversine_m(geometry[i - 1][1], geometry[i - 1][0], geometry[i][1], geometry[i][0])
            total_dist += seg_len
            cum_dist.append(total_dist)

        if total_dist <= 0:
            return 50.0, 0.0, []

        # Divide route into 250-meter bins
        bin_size_m = 250.0
        num_bins = max(1, math.ceil(total_dist / bin_size_m))
        bin_counts = [0] * num_bins

        # Project places onto nearest polyline vertex to determine progress along route
        for p in places:
            # Only count open or likely open places towards active distribution
            if p.is_likely_open is False:
                continue

            # Find closest vertex
            best_idx = 0
            min_d = float("inf")
            for idx, pt in enumerate(geometry):
                d = haversine_m(p.latitude, p.longitude, pt[1], pt[0])
                if d < min_d:
                    min_d = d
                    best_idx = idx

            pos_m = cum_dist[best_idx]
            bin_idx = min(num_bins - 1, int(pos_m / bin_size_m))
            bin_counts[bin_idx] += 1

        # Fraction of bins having at least one establishment
        active_bins = sum(1 for c in bin_counts if c > 0)
        coverage_ratio = active_bins / float(num_bins)

        # Base distribution score
        dist_score = min(100.0, coverage_ratio * 105.0)

        # Detect consecutive empty bins (long unsupported stretches)
        longest_gap_m = 0.0
        current_gap_m = 0.0
        low_activity_segments: List[LowActivitySegment] = []
        gap_start_bin = -1

        for b_idx, count in enumerate(bin_counts):
            if count == 0:
                if gap_start_bin == -1:
                    gap_start_bin = b_idx
                current_gap_m += bin_size_m
            else:
                if current_gap_m > longest_gap_m:
                    longest_gap_m = current_gap_m
                if current_gap_m >= max_acceptable_gap_m and gap_start_bin != -1:
                    # Map bin indices back to polyline coordinates
                    start_m = gap_start_bin * bin_size_m
                    end_m = b_idx * bin_size_m
                    # Find vertices
                    v_start = min(range(len(cum_dist)), key=lambda k: abs(cum_dist[k] - start_m))
                    v_end = min(range(len(cum_dist)), key=lambda k: abs(cum_dist[k] - end_m))

                    low_activity_segments.append(
                        LowActivitySegment(
                            start_coordinates=Coordinates(latitude=geometry[v_start][1], longitude=geometry[v_start][0]),
                            end_coordinates=Coordinates(latitude=geometry[v_end][1], longitude=geometry[v_end][0]),
                            length_meters=round(current_gap_m, 1),
                            description=f"Low mapped activity segment of ~{int(current_gap_m)}m with limited open establishments.",
                        )
                    )
                current_gap_m = 0.0
                gap_start_bin = -1

        # Check trailing gap
        if current_gap_m > longest_gap_m:
            longest_gap_m = current_gap_m
        if current_gap_m >= max_acceptable_gap_m and gap_start_bin != -1:
            start_m = gap_start_bin * bin_size_m
            v_start = min(range(len(cum_dist)), key=lambda k: abs(cum_dist[k] - start_m))
            v_end = len(geometry) - 1
            low_activity_segments.append(
                LowActivitySegment(
                    start_coordinates=Coordinates(latitude=geometry[v_start][1], longitude=geometry[v_start][0]),
                    end_coordinates=Coordinates(latitude=geometry[v_end][1], longitude=geometry[v_end][0]),
                    length_meters=round(current_gap_m, 1),
                    description=f"Low mapped activity segment of ~{int(current_gap_m)}m towards arrival destination.",
                )
            )

        # Penalize distribution score if there is a very long gap
        if longest_gap_m > 500.0:
            dist_score = max(10.0, dist_score - 25.0)
        elif longest_gap_m > 300.0:
            dist_score = max(20.0, dist_score - 12.0)

        return round(dist_score, 1), round(longest_gap_m, 1), low_activity_segments

    def evaluate_route(
        self,
        geometry: List[List[float]],
        distance_meters: float,
        road_summary: str,
        places: List[NearbyPlace],
        mode: TravelMode = TravelMode.walking,
        has_cctv_data: bool = False,
    ) -> RouteScoreBreakdown:
        """Calculates normalized component scores and the final 0-100 Night Activity Score."""
        # 1. Component: Open Establishments
        open_count = sum(1 for p in places if p.is_likely_open is True)
        unknown_count = sum(1 for p in places if p.hours_unknown is True)
        total_eval_places = len(places)

        if total_eval_places == 0 and distance_meters > 200:
            # Insufficient empirical data available along corridor
            return RouteScoreBreakdown(
                overall_night_activity_score=None,
                score_label="Score Unavailable (Limited Data)",
                is_score_available=False,
                components={},
                open_establishments_count=0,
                unknown_hours_count=0,
                assistance_points_count=0,
                transit_points_count=0,
                longest_low_activity_segment_meters=round(distance_meters, 1),
                low_activity_segments_count=1,
                road_characteristics_summary=road_summary or "Unclassified Road Segment",
                disclaimer="Insufficient mapped data along this route corridor to compute an empirical score without fabrication.",
            )

        # Benchmark density: 1 open place per 300m for walking, 1 per 600m for driving
        density_target = distance_meters / (300.0 if mode == TravelMode.walking else 600.0)
        density_target = max(1.0, density_target)

        # Open establishments score: weighted for confirmed open plus partial credit for unknown
        effective_open = open_count + (unknown_count * 0.4)
        score_open = min(100.0, (effective_open / density_target) * 75.0 + (15.0 if open_count > 0 else 0.0))

        # 2. Component: Distribution of Establishments
        max_gap_threshold = 300.0 if mode == TravelMode.walking else 700.0
        score_dist, longest_gap, low_act_segments = self._calculate_distribution_and_gaps(
            geometry, places, max_acceptable_gap_m=max_gap_threshold
        )

        # 3. Component: Road Characteristics
        score_road = 65.0
        road_lower = (road_summary or "").lower()
        if any(kw in road_lower for kw in ["main", "boulevard", "arterial", "express", "ring", "avenue"]):
            score_road = 88.0
        elif any(kw in road_lower for kw in ["residential", "commercial"]):
            score_road = 75.0
        elif any(kw in road_lower for kw in ["secondary", "link"]):
            score_road = 70.0
        elif any(kw in road_lower for kw in ["alley", "path", "unpaved"]):
            score_road = 40.0

        # 4. Component: Nearby Assistance Points
        police_count = sum(1 for p in places if p.category == PlaceCategory.police)
        hospital_count = sum(1 for p in places if p.category == PlaceCategory.hospital)
        assistance_total = police_count + hospital_count

        if assistance_total >= 3:
            score_assist = 95.0
        elif assistance_total == 2:
            score_assist = 85.0
        elif assistance_total == 1:
            score_assist = 72.0
        else:
            score_assist = 45.0  # Mapped baseline

        # 5. Component: Public Transport
        transit_count = sum(1 for p in places if p.category == PlaceCategory.transit)
        if transit_count >= 3:
            score_transit = 90.0
        elif transit_count >= 1:
            score_transit = 74.0
        else:
            score_transit = 40.0

        # 6. Component: CCTV
        cctv_count = sum(1 for p in places if p.category == PlaceCategory.cctv)
        cctv_available = has_cctv_data and cctv_count > 0
        if cctv_available:
            if cctv_count >= 3:
                score_cctv = 95.0
            elif cctv_count == 2:
                score_cctv = 85.0
            else:
                score_cctv = 70.0
            cctv_desc = f"{cctv_count} verified surveillance camera(s) / campus CCTV post(s) mapped along corridor."
        else:
            score_cctv = 0.0
            cctv_desc = "Verified public CCTV coverage data is currently unmapped for this corridor (weight cleanly redistributed)."

        # Build raw components dictionary
        raw_components = {
            "open_establishments": (
                score_open,
                True,
                f"{open_count} mapped establishments likely open; {unknown_count} with unmapped hours.",
            ),
            "distribution": (
                score_dist,
                True,
                f"Longest stretch with low mapped activity is ~{int(longest_gap)} meters.",
            ),
            "road_characteristics": (
                score_road,
                True,
                f"Route features {road_summary}.",
            ),
            "assistance_points": (
                score_assist,
                True,
                f"{police_count} police assistance nodes and {hospital_count} medical facilities within corridor.",
            ),
            "public_transport": (
                score_transit,
                True,
                f"{transit_count} transit stops / metro stations nearby.",
            ),
            "cctv": (
                score_cctv,
                cctv_available,
                cctv_desc,
            ),
        }

        # Weight selection based on travel mode
        base_weights = self.weights_walking if mode == TravelMode.walking else self.weights_driving

        # Renormalize weights strictly over available components
        available_weight_sum = sum(base_weights[k] for k, (_, avail, _) in raw_components.items() if avail)
        if available_weight_sum <= 0:
            available_weight_sum = 1.0

        components_dict: Dict[str, ScoreComponent] = {}
        weighted_total = 0.0

        for key, (raw_score, is_avail, desc) in raw_components.items():
            base_w = base_weights.get(key, 0.0)
            if is_avail:
                norm_w = base_w / available_weight_sum
                weighted_total += raw_score * norm_w
            else:
                norm_w = 0.0

            components_dict[key] = ScoreComponent(
                name=key.replace("_", " ").title(),
                score=round(raw_score, 1),
                weight=round(norm_w, 3),
                available=is_avail,
                description=desc,
            )

        final_score = int(round(max(0.0, min(100.0, weighted_total))))

        # Determine qualitative label
        if final_score >= 75:
            label = "Higher Mapped Night Activity"
        elif final_score >= 50:
            label = "Moderate Night Activity"
        else:
            label = "Lower Mapped Night Activity"

        return RouteScoreBreakdown(
            overall_night_activity_score=final_score,
            score_label=label,
            is_score_available=True,
            components=components_dict,
            open_establishments_count=open_count,
            unknown_hours_count=unknown_count,
            assistance_points_count=assistance_total,
            transit_points_count=transit_count,
            cctv_points_count=cctv_count,
            longest_low_activity_segment_meters=longest_gap,
            low_activity_segments_count=len(low_act_segments),
            road_characteristics_summary=road_summary,
            disclaimer="This score summarizes mapped activity and support indicators along the route. It does not predict crime or guarantee personal safety.",
        )


night_activity_service = NightActivityService()
