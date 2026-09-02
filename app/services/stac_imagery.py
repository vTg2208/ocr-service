"""Bounded and allow-listed STAC discovery for historical FRA evidence."""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import json
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from shapely.geometry import shape


class STACConfigurationError(ValueError):
    pass


class STACProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class SceneCandidate:
    scene_id: str
    provider: str
    collection: str
    acquired_at: datetime
    footprint: dict
    cloud_cover: float | None
    asset_keys: tuple[str, ...]
    license_reference: str | None
    private_asset_references: dict = field(repr=False, default_factory=dict)


def _default_transport(endpoint: str, payload: dict, timeout: float) -> dict:
    request = Request(endpoint, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "Accept": "application/geo+json, application/json"}, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - endpoint allow-listed.
            return json.loads(response.read().decode("utf-8"))
    except Exception as error:
        raise STACProviderError("STAC provider request failed.") from error


def _date_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _footprint(value: dict) -> dict:
    if not isinstance(value, dict) or value.get("type") not in {"Polygon", "MultiPolygon"}:
        raise ValueError("scene footprint is not polygonal")
    normalized = {"type": "MultiPolygon", "coordinates": [value["coordinates"]]} if value["type"] == "Polygon" else value
    parsed = shape(normalized)
    if parsed.is_empty or not parsed.is_valid:
        raise ValueError("scene footprint is invalid")
    return normalized


class STACClient:
    def __init__(
        self,
        endpoint: str,
        *,
        allowed_hosts: set[str],
        allowed_collections: set[str],
        transport=None,
        timeout_seconds: float = 20,
        max_pages: int = 5,
        max_results: int = 100,
    ):
        self.endpoint = endpoint.strip()
        self.allowed_hosts = {host.casefold() for host in allowed_hosts}
        self.allowed_collections = set(allowed_collections)
        self.transport = transport or _default_transport
        self.timeout_seconds = float(timeout_seconds)
        self.max_pages = int(max_pages)
        self.max_results = int(max_results)
        self.last_warnings = []
        self._validate_url(self.endpoint, label="STAC endpoint")
        if not 0 < self.timeout_seconds <= 60 or not 1 <= self.max_pages <= 20 or not 1 <= self.max_results <= 500:
            raise STACConfigurationError("STAC timeout and result bounds are invalid.")

    def _validate_url(self, value: str, *, label: str) -> None:
        parsed = urlparse(value)
        local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
        if (parsed.scheme != "https" and not local_http) or not parsed.hostname or parsed.hostname.casefold() not in self.allowed_hosts:
            raise STACConfigurationError(f"{label} host must be allow-listed and use HTTPS.")

    def search(self, geometry: dict, date_range: tuple[date, date], collections: list[str], max_cloud: float) -> list[SceneCandidate]:
        if not isinstance(geometry, dict) or geometry.get("type") != "MultiPolygon" or shape(geometry).is_empty:
            raise ValueError("STAC search requires a non-empty GeoJSON MultiPolygon.")
        start, end = date_range
        if start > end:
            raise ValueError("STAC date range is invalid.")
        requested = list(dict.fromkeys(collections))
        if not requested or not set(requested).issubset(self.allowed_collections):
            raise STACConfigurationError("STAC collection is not allow-listed.")
        if not 0 <= float(max_cloud) <= 100:
            raise ValueError("Maximum cloud cover must be between 0 and 100.")
        payload = {
            "intersects": geometry,
            "datetime": f"{start.isoformat()}T00:00:00Z/{end.isoformat()}T23:59:59Z",
            "collections": requested,
            "query": {"eo:cloud_cover": {"lte": float(max_cloud)}},
            "limit": min(100, self.max_results),
        }
        endpoint = self.endpoint
        scenes = []
        self.last_warnings = []
        for _page in range(self.max_pages):
            response = self.transport(endpoint, payload, self.timeout_seconds)
            if not isinstance(response, dict) or not isinstance(response.get("features"), list):
                raise STACProviderError("STAC provider returned malformed search data.")
            for raw in response["features"]:
                if len(scenes) >= self.max_results:
                    break
                try:
                    properties = raw["properties"]
                    acquired = _date_time(properties["datetime"])
                    cloud = properties.get("eo:cloud_cover")
                    cloud = float(cloud) if cloud is not None else None
                    if cloud is not None and cloud > float(max_cloud):
                        self.last_warnings.append(f"Skipped {raw.get('id', 'scene')}: cloud threshold.")
                        continue
                    collection = str(raw["collection"])
                    if collection not in requested:
                        raise ValueError("unexpected collection")
                    assets = raw.get("assets") or {}
                    if not isinstance(assets, dict):
                        raise ValueError("assets are invalid")
                    license_reference = next((link.get("href") for link in raw.get("links", []) if link.get("rel") == "license"), None)
                    scenes.append(SceneCandidate(
                        scene_id=str(raw["id"]), provider=urlparse(self.endpoint).hostname or "stac",
                        collection=collection, acquired_at=acquired,
                        footprint=_footprint(raw["geometry"]), cloud_cover=cloud,
                        asset_keys=tuple(sorted(assets)), license_reference=license_reference,
                        private_asset_references={key: dict(value) for key, value in assets.items()},
                    ))
                except (KeyError, TypeError, ValueError) as error:
                    self.last_warnings.append(f"Skipped malformed STAC item: {error}.")
            if len(scenes) >= self.max_results:
                break
            next_link = next((link for link in response.get("links", []) if link.get("rel") == "next"), None)
            if not next_link:
                break
            endpoint = str(next_link.get("href") or "")
            self._validate_url(endpoint, label="STAC next link")
            if isinstance(next_link.get("body"), dict):
                payload = next_link["body"]
        midpoint = start.toordinal() + (end.toordinal() - start.toordinal()) / 2
        scenes.sort(key=lambda scene: (scene.cloud_cover if scene.cloud_cover is not None else 101, abs(scene.acquired_at.date().toordinal() - midpoint), scene.scene_id))
        return scenes


__all__ = ["STACClient", "STACConfigurationError", "STACProviderError", "SceneCandidate"]
