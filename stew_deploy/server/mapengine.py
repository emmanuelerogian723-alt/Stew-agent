"""
S.T.E.W Map Engine — real, free, open-source mapping and location tools.

No API keys, no billing, no vendor lock-in:
- Geocoding / reverse geocoding: OpenStreetMap Nominatim (public instance)
- Routing (distance + duration + path): OSRM public demo server
- Points of interest ("what's near me"): Overpass API (OpenStreetMap POI database)
- Rendering: `staticmap` library drawing real OpenStreetMap tiles, with an
  Esri World Imagery satellite tile template as a free, no-key alternative.

This replaces the old behavior where the chat LLM would just hallucinate a
fake `{"tool": "generate_image", ...}` JSON blob when asked for a map — there
was no real map tool wired up. Now /map, /satmap, /nearby, /findme, /track,
/trackmap and /trackstatus call these functions and return a REAL, accurate
map image plus real distances/addresses.
"""
import io
import math
import logging
from typing import Optional

import httpx
from staticmap import StaticMap, CircleMarker, Line

logger = logging.getLogger(__name__)

# Nominatim's usage policy requires a descriptive User-Agent identifying the
# application (not a browser UA) — using a generic one gets silently blocked.
_UA = "StewAgent/1.0 (+https://stew-agent.onrender.com; contact: emmanuelerogian723@gmail.com)"
_HEADERS = {"User-Agent": _UA}

NOMINATIM_SEARCH = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE = "https://nominatim.openstreetmap.org/reverse"
OSRM_ROUTE = "https://router.project-osrm.org/route/v1/driving/{coords}"
OVERPASS_API = "https://overpass-api.de/api/interpreter"

OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
# Esri World Imagery — free, no API key, usable for general/non-heavy apps.
SATELLITE_TILE_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"

# Overpass POI categories a person who is lost or in trouble is most likely
# to need, mapped to OSM tag queries.
NEARBY_CATEGORIES = {
    "hospital": '"amenity"="hospital"',
    "pharmacy": '"amenity"="pharmacy"',
    "police": '"amenity"="police"',
    "fuel": '"amenity"="fuel"',
    "atm": '"amenity"="atm"',
    "bank": '"amenity"="bank"',
    "restaurant": '"amenity"="restaurant"',
    "hotel": '"tourism"="hotel"',
    "bus_station": '"amenity"="bus_station"',
    "toilet": '"amenity"="toilets"',
}


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


OPEN_METEO_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"


async def _geocode_nominatim(query: str) -> Optional[dict]:
    async with httpx.AsyncClient(timeout=10, headers=_HEADERS) as client:
        resp = await client.get(NOMINATIM_SEARCH, params={
            "q": query, "format": "json", "limit": 1, "addressdetails": 0,
        })
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return None
        hit = data[0]
        return {
            "lat": float(hit["lat"]),
            "lon": float(hit["lon"]),
            "display_name": hit.get("display_name", query),
        }


async def _geocode_open_meteo(query: str) -> Optional[dict]:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(OPEN_METEO_GEOCODE, params={"name": query, "count": 1})
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results") or []
        if not results:
            return None
        hit = results[0]
        parts = [hit.get("name", query)]
        if hit.get("admin1") and hit["admin1"] != hit.get("name"):
            parts.append(hit["admin1"])
        if hit.get("country"):
            parts.append(hit["country"])
        return {
            "lat": float(hit["latitude"]),
            "lon": float(hit["longitude"]),
            "display_name": ", ".join(parts),
        }


async def geocode(query: str) -> Optional[dict]:
    """
    Turn a place name / address into {lat, lon, display_name}. Free, no key.

    Two independent providers, tried in order, so a single provider having a
    bad day (rate limiting, cloud-IP blocking, transient outage) doesn't take
    the whole /map feature down:
      1. Open-Meteo Geocoding — very reliable from datacenter/cloud IPs, great
         city/town coverage worldwide, no usage-policy rate limiting.
      2. Nominatim — better for precise/full addresses when Open-Meteo has no
         match, but is more sensitive to shared-IP rate limiting.
    """
    query = (query or "").strip()
    if not query:
        return None

    for provider in (_geocode_open_meteo, _geocode_nominatim):
        try:
            result = await provider(query)
            if result:
                return result
        except Exception as e:
            logger.warning(f"geocode provider {provider.__name__} failed for {query!r}: {e}")
            continue
    return None


BIGDATACLOUD_REVERSE = "https://api.bigdatacloud.net/data/reverse-geocode-client"


async def reverse_geocode(lat: float, lon: float) -> Optional[str]:
    """
    Turn coordinates into a human-readable address. Free, no key. Two
    independent providers for the same resilience reason as geocode() above.
    """
    try:
        async with httpx.AsyncClient(timeout=10, headers=_HEADERS) as client:
            resp = await client.get(NOMINATIM_REVERSE, params={
                "lat": lat, "lon": lon, "format": "json", "zoom": 16,
            })
            resp.raise_for_status()
            data = resp.json()
            if data.get("display_name"):
                return data["display_name"]
    except Exception as e:
        logger.warning(f"reverse_geocode/Nominatim failed for {lat},{lon}: {e}")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(BIGDATACLOUD_REVERSE, params={
                "latitude": lat, "longitude": lon, "localityLanguage": "en",
            })
            resp.raise_for_status()
            data = resp.json()
            parts = [p for p in (data.get("locality"), data.get("city"), data.get("principalSubdivision"), data.get("countryName")) if p]
            if parts:
                return ", ".join(dict.fromkeys(parts))  # dedupe while preserving order
    except Exception as e:
        logger.warning(f"reverse_geocode/BigDataCloud failed for {lat},{lon}: {e}")

    return None


async def route(origin: dict, dest: dict) -> Optional[dict]:
    """
    Real driving route between two geocoded points via OSRM (free, no key).
    origin/dest: {"lat":.., "lon":..} as returned by geocode().
    Returns {"distance_km", "duration_min", "geometry": [(lat,lon), ...]}.
    """
    coords = f"{origin['lon']},{origin['lat']};{dest['lon']},{dest['lat']}"
    url = OSRM_ROUTE.format(coords=coords)
    try:
        async with httpx.AsyncClient(timeout=15, headers=_HEADERS) as client:
            resp = await client.get(url, params={"overview": "full", "geometries": "geojson"})
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "Ok" or not data.get("routes"):
                return None
            r = data["routes"][0]
            geometry = [(lat, lon) for lon, lat in r["geometry"]["coordinates"]]
            return {
                "distance_km": round(r["distance"] / 1000, 1),
                "duration_min": round(r["duration"] / 60),
                "geometry": geometry,
            }
    except Exception as e:
        logger.warning(f"route failed {origin} -> {dest}: {e}")
        return None


async def _overpass_query(lat, lon, tag, radius_m, limit):
    ql = f"""
    [out:json][timeout:15];
    node[{tag}](around:{radius_m},{lat},{lon});
    out body {limit * 4};
    """
    try:
        async with httpx.AsyncClient(timeout=20, headers=_HEADERS) as client:
            resp = await client.post(OVERPASS_API, data={"data": ql})
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning(f"nearby/Overpass failed for {tag} near {lat},{lon} r={radius_m}: {e}")
        return []

    results = []
    for el in data.get("elements", []):
        name = (el.get("tags") or {}).get("name")
        if not name:
            continue
        d = _haversine_km(lat, lon, el["lat"], el["lon"])
        results.append({"name": name, "lat": el["lat"], "lon": el["lon"], "distance_km": round(d, 2)})
    results.sort(key=lambda x: x["distance_km"])
    return results[:limit]


async def nearby(lat: float, lon: float, category: str = "hospital", radius_m: int = 4000, limit: int = 5) -> list:
    """
    Find real nearby points of interest via Overpass (OpenStreetMap POI
    database) — free, no key. category must be one of NEARBY_CATEGORIES.
    Auto-expands the search radius (4km -> 10km -> 20km) if nothing is found
    nearby, since OSM coverage density varies a lot by category and area —
    someone lost in a rural area still deserves an answer, not an empty list.
    Returns a list of {"name", "lat", "lon", "distance_km"} sorted by distance.
    """
    tag = NEARBY_CATEGORIES.get(category.lower(), NEARBY_CATEGORIES["hospital"])
    for r in (radius_m, max(radius_m, 10000), 20000):
        results = await _overpass_query(lat, lon, tag, r, limit)
        if results:
            return results
    return []


def render_map(markers: list, route_geometry: Optional[list] = None,
                satellite: bool = False, width: int = 800, height: int = 600,
                zoom: Optional[int] = None) -> bytes:
    """
    Render a real map image (actual OSM/satellite tiles, not AI-generated).
    markers: list of (lat, lon, color) tuples.
    route_geometry: optional list of (lat, lon) to draw as a connecting line.
    Returns PNG bytes.
    """
    tile_url = SATELLITE_TILE_URL if satellite else OSM_TILE_URL
    m = StaticMap(width, height, url_template=tile_url, headers=_HEADERS, tile_request_timeout=12)

    if route_geometry and len(route_geometry) >= 2:
        line_coords = [(lon, lat) for lat, lon in route_geometry]
        m.add_line(Line(line_coords, "#2563eb", 5))

    for lat, lon, color in markers:
        m.add_marker(CircleMarker((lon, lat), color, 14))
        m.add_marker(CircleMarker((lon, lat), "#ffffff", 6))

    image = m.render(zoom=zoom) if zoom else m.render()
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
