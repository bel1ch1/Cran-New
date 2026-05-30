"""Position-based landmark map with spatial trust for bridge XY calibration."""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean, pstdev

from app.services.camera_config import (
    spatial_max_trust_sigma_m,
    spatial_merge_tolerance_m,
    spatial_min_landmark_separation_m,
    spatial_min_trust_hits,
    spatial_runtime_match_tolerance_m,
)

MAX_CANDIDATE_OBSERVATIONS = 30


def parse_bridge_axis_sign(movement_direction: str) -> int:
    """Return +1 for normal axis, -1 for reversed (from calibration movement_direction)."""
    if "axis=reversed" in str(movement_direction or ""):
        return -1
    return 1


@dataclass
class TrustedLandmark:
    x_m: float
    hits: int = field(default_factory=spatial_min_trust_hits)
    trust: float = 1.0


@dataclass
class CandidateCluster:
    observations: list[float] = field(default_factory=list)

    @property
    def hits(self) -> int:
        return len(self.observations)

    @property
    def mean_x(self) -> float:
        return mean(self.observations)

    @property
    def sigma(self) -> float:
        if len(self.observations) <= 1:
            return 0.0
        return pstdev(self.observations)

    def add(self, abs_x_m: float) -> None:
        self.observations.append(float(abs_x_m))
        if len(self.observations) > MAX_CANDIDATE_OBSERVATIONS:
            self.observations = self.observations[-MAX_CANDIDATE_OBSERVATIONS:]


class SpatialMarkerMap:
    """Build and use a slot-indexed landmark map from spatial observations."""

    def __init__(
        self,
        reference_marker_id: int = 0,
        zero_marker_offset_m: float = 0.0,
    ) -> None:
        self.reference_marker_id = int(reference_marker_id)
        self.zero_marker_offset_m = float(zero_marker_offset_m)
        self._trusted: list[TrustedLandmark] = []
        self._candidates: list[CandidateCluster] = []

    @property
    def trusted_landmarks(self) -> list[TrustedLandmark]:
        return list(self._trusted)

    @property
    def known_count(self) -> int:
        return len(self._trusted)

    def apply_zero_offset(self, offset_m: float) -> None:
        target = float(offset_m)
        if not self._trusted:
            self.zero_marker_offset_m = target
            return
        current = self._sorted_trusted()[0].x_m
        delta = target - current
        if abs(delta) <= 1e-9:
            self.zero_marker_offset_m = target
            return
        for landmark in self._trusted:
            landmark.x_m = round(landmark.x_m + delta, 6)
        self.zero_marker_offset_m = target

    def set_reference_marker_id(self, marker_id: int) -> None:
        self.reference_marker_id = int(marker_id)

    def _sorted_trusted(self) -> list[TrustedLandmark]:
        return sorted(self._trusted, key=lambda item: item.x_m)

    def _trust_score(self, hits: int, sigma: float) -> float:
        hit_ratio = min(1.0, hits / max(1, spatial_min_trust_hits()))
        sigma_ratio = max(0.0, 1.0 - sigma / max(1e-6, spatial_max_trust_sigma_m()))
        return round(min(1.0, hit_ratio * sigma_ratio), 3)

    def _nearest_trusted(self, abs_x_m: float, tolerance: float) -> TrustedLandmark | None:
        best: TrustedLandmark | None = None
        best_dist = tolerance
        for landmark in self._trusted:
            dist = abs(landmark.x_m - abs_x_m)
            if dist <= best_dist:
                best_dist = dist
                best = landmark
        return best

    def _nearest_candidate(self, abs_x_m: float) -> tuple[CandidateCluster | None, float]:
        best: CandidateCluster | None = None
        best_dist = spatial_merge_tolerance_m()
        for cluster in self._candidates:
            dist = abs(cluster.mean_x - abs_x_m)
            if dist <= best_dist:
                best_dist = dist
                best = cluster
        return best, best_dist

    def _too_close_to_trusted(self, abs_x_m: float, exclude_x: float | None = None) -> bool:
        for landmark in self._trusted:
            if exclude_x is not None and abs(landmark.x_m - exclude_x) <= 1e-6:
                continue
            if abs(landmark.x_m - abs_x_m) < spatial_min_landmark_separation_m():
                return True
        return False

    def _confirm_cluster(self, cluster: CandidateCluster) -> TrustedLandmark | None:
        if cluster.hits < spatial_min_trust_hits():
            return None
        sigma = cluster.sigma
        if sigma > spatial_max_trust_sigma_m():
            return None
        candidate_x = round(cluster.mean_x, 4)
        existing = self._nearest_trusted(candidate_x, spatial_min_landmark_separation_m())
        if existing is not None:
            existing.hits = max(existing.hits, cluster.hits)
            existing.trust = self._trust_score(existing.hits, sigma)
            return None
        if self._too_close_to_trusted(candidate_x):
            return None
        landmark = TrustedLandmark(
            x_m=candidate_x,
            hits=cluster.hits,
            trust=self._trust_score(cluster.hits, sigma),
        )
        self._trusted.append(landmark)
        self._trusted.sort(key=lambda item: item.x_m)
        return landmark

    def estimate_camera_x(self, observations: list[dict], axis_sign: int) -> float | None:
        if not observations:
            return None
        estimates: list[float] = []
        ref_obs = next((obs for obs in observations if int(obs["id"]) == self.reference_marker_id), None)
        if ref_obs is not None:
            estimates.append(self.zero_marker_offset_m - (axis_sign * float(ref_obs["x_rel_m"])))
        for obs in observations:
            rel_x = float(obs["x_rel_m"])
            for landmark in self._trusted:
                estimates.append(landmark.x_m - (axis_sign * rel_x))
        if not estimates:
            return None
        return mean(estimates)

    def _abs_x_for_observation(
        self,
        obs: dict,
        camera_x_m: float | None,
        axis_sign: int,
    ) -> float | None:
        marker_id = int(obs["id"])
        if marker_id == self.reference_marker_id:
            return self.zero_marker_offset_m
        if camera_x_m is None:
            return None
        return camera_x_m + (axis_sign * float(obs["x_rel_m"]))

    def ingest_observations(
        self,
        observations: list[dict],
        camera_x_m: float | None,
        calibration_enabled: bool,
        axis_sign: int,
    ) -> tuple[str, dict[str, float]]:
        if not observations:
            return "Маркеры не найдены", {}

        if camera_x_m is None:
            camera_x_m = self.estimate_camera_x(observations, axis_sign)

        if not calibration_enabled:
            if camera_x_m is not None:
                return "Мониторинг маркеров", {}
            return "Ожидание опорного маркера", {}

        newly_confirmed: dict[str, float] = {}
        added_any = False

        for obs in observations:
            abs_x = self._abs_x_for_observation(obs, camera_x_m, axis_sign)
            if abs_x is None:
                continue

            cluster, _ = self._nearest_candidate(abs_x)
            if cluster is None:
                cluster = CandidateCluster()
                self._candidates.append(cluster)
            cluster.add(abs_x)
            added_any = True

        if not added_any:
            if camera_x_m is None:
                return "Ожидание опорного или подтверждённого маркера", {}
            return "Накопление наблюдений", {}

        confirmed_this_frame: list[TrustedLandmark] = []
        for cluster in list(self._candidates):
            landmark = self._confirm_cluster(cluster)
            if landmark is not None:
                confirmed_this_frame.append(landmark)
                self._candidates.remove(cluster)

        if confirmed_this_frame:
            slot_map = self.to_marker_positions_m()
            for landmark in confirmed_this_frame:
                for slot, x_val in slot_map.items():
                    if abs(x_val - landmark.x_m) <= spatial_merge_tolerance_m():
                        newly_confirmed[slot] = x_val
                        break
            parts = []
            for slot, x_val in sorted(newly_confirmed.items(), key=lambda item: int(item[0])):
                trust = next(
                    (lm.trust for lm in self._trusted if abs(lm.x_m - x_val) <= spatial_merge_tolerance_m()),
                    1.0,
                )
                hits = next(
                    (lm.hits for lm in self._trusted if abs(lm.x_m - x_val) <= spatial_merge_tolerance_m()),
                    spatial_min_trust_hits(),
                )
                parts.append(f"Точка {slot}: X={x_val:.3f} м (доверие {hits})")
            return f"Подтверждена точка: {', '.join(parts)}", newly_confirmed

        pending = max((c.hits for c in self._candidates), default=0)
        return f"Накопление наблюдений ({pending}/{spatial_min_trust_hits()})", {}

    def to_marker_positions_m(self) -> dict[str, float]:
        sorted_landmarks = self._sorted_trusted()
        return {str(idx): round(landmark.x_m, 4) for idx, landmark in enumerate(sorted_landmarks)}

    def landmark_trust(self) -> dict[str, float]:
        sorted_landmarks = self._sorted_trusted()
        return {str(idx): landmark.trust for idx, landmark in enumerate(sorted_landmarks)}

    @classmethod
    def from_marker_positions_m(
        cls,
        data: dict[str, float],
        reference_marker_id: int,
        zero_offset: float,
    ) -> SpatialMarkerMap:
        instance = cls(
            reference_marker_id=reference_marker_id,
            zero_marker_offset_m=zero_offset,
        )
        if not data:
            return instance
        try:
            sorted_items = sorted(((str(k), float(v)) for k, v in data.items()), key=lambda item: item[1])
        except (TypeError, ValueError):
            sorted_items = []
        for _, x_m in sorted_items:
            instance._trusted.append(
                TrustedLandmark(
                    x_m=round(x_m, 6),
                    hits=spatial_min_trust_hits(),
                    trust=1.0,
                )
            )
        instance._trusted.sort(key=lambda item: item.x_m)
        return instance

    def trusted_x_positions(self) -> list[float]:
        return [landmark.x_m for landmark in self._sorted_trusted()]

    def match_landmark_for_detection(
        self,
        rel_x_m: float,
        camera_x_m: float,
        axis_sign: int,
        *,
        precomputed_abs: float | None = None,
        tolerance: float | None = None,
    ) -> float | None:
        tolerance = spatial_runtime_match_tolerance_m() if tolerance is None else tolerance
        abs_x = precomputed_abs if precomputed_abs is not None else camera_x_m + (axis_sign * rel_x_m)
        matched = self._nearest_trusted(abs_x, tolerance)
        if matched is None:
            return None
        return matched.x_m

    @classmethod
    def create_for_calibration_session(
        cls,
        *,
        reference_marker_id: int,
        zero_offset_m: float,
    ) -> SpatialMarkerMap:
        """Start a calibration session with the reference landmark anchored at zero offset."""
        instance = cls(
            reference_marker_id=int(reference_marker_id),
            zero_marker_offset_m=float(zero_offset_m),
        )
        instance._trusted.append(
            TrustedLandmark(
                x_m=round(float(zero_offset_m), 6),
                hits=spatial_min_trust_hits(),
                trust=1.0,
            )
        )
        instance._trusted.sort(key=lambda item: item.x_m)
        return instance


def _observation_marker_id(obs: dict) -> int:
    if "id" in obs:
        return int(obs["id"])
    return int(obs["marker_id"])


def _observation_rel_x_m(obs: dict) -> float:
    if "x_rel_m" in obs:
        return float(obs["x_rel_m"])
    return float(obs["rel_x_m"])


def _observation_distance_m(obs: dict) -> float:
    return float(obs.get("distance_m") or 1.0)


def _weight_from_distance(distance_m: float) -> float:
    return 1.0 / (0.05 + distance_m * distance_m)


def _bootstrap_camera_x_hint(
    observations: list[dict],
    spatial_map: SpatialMarkerMap,
    axis_sign: int,
    match_tolerance_m: float,
) -> float | None:
    """Estimate camera X when the reference marker is not visible and there is no prior hint."""
    trusted = spatial_map.trusted_x_positions()
    if not trusted or not observations:
        return None

    candidates: list[float] = []
    for obs in observations:
        rel_x_m = _observation_rel_x_m(obs)
        for landmark_x in trusted:
            candidates.append(landmark_x - (axis_sign * rel_x_m))
    if not candidates:
        return None

    center = float(mean(candidates))
    inlier_window = max(match_tolerance_m * 2.0, spatial_min_landmark_separation_m())
    inliers = [value for value in candidates if abs(value - center) <= inlier_window]
    return float(mean(inliers)) if inliers else center


def collect_camera_x_estimates(
    observations: list[dict],
    spatial_map: SpatialMarkerMap,
    *,
    axis_sign: int,
    hint_x_m: float | None = None,
    match_tolerance_m: float | None = None,
) -> list[tuple[float, float]]:
    """Return weighted camera-X estimates using spatial landmarks (ArUco ID order not required)."""
    if match_tolerance_m is None:
        match_tolerance_m = spatial_runtime_match_tolerance_m()
    weighted: list[tuple[float, float]] = []

    reference_estimates: list[float] = []
    for obs in observations:
        marker_id = _observation_marker_id(obs)
        if marker_id != spatial_map.reference_marker_id:
            continue
        rel_x_m = _observation_rel_x_m(obs)
        estimate = spatial_map.zero_marker_offset_m - (axis_sign * rel_x_m)
        reference_estimates.append(estimate)
        weighted.append((estimate, _weight_from_distance(_observation_distance_m(obs))))

    hint = mean(reference_estimates) if reference_estimates else hint_x_m
    if hint is None:
        hint = _bootstrap_camera_x_hint(
            observations,
            spatial_map,
            axis_sign,
            match_tolerance_m,
        )

    for obs in observations:
        marker_id = _observation_marker_id(obs)
        if marker_id == spatial_map.reference_marker_id:
            continue
        if hint is None:
            continue
        rel_x_m = _observation_rel_x_m(obs)
        landmark_x = spatial_map.match_landmark_for_detection(
            rel_x_m,
            hint,
            axis_sign,
            tolerance=match_tolerance_m,
        )
        if landmark_x is None:
            continue
        estimate = landmark_x - (axis_sign * rel_x_m)
        weighted.append((estimate, _weight_from_distance(_observation_distance_m(obs))))

    return weighted


def fuse_camera_x_estimate(weighted_estimates: list[tuple[float, float]]) -> float | None:
    if not weighted_estimates:
        return None
    total_weight = sum(weight for _, weight in weighted_estimates)
    if total_weight <= 0.0:
        return float(mean(value for value, _ in weighted_estimates))
    return sum(value * weight for value, weight in weighted_estimates) / total_weight
