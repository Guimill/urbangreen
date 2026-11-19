"""French communes from the official API (geo.api.gouv.fr, no key needed)."""
import requests

API = "https://geo.api.gouv.fr/communes"
HEADERS = {"User-Agent": "urbangreen/0.1 (Sentinel-2 urban green monitor)"}
FIELDS = "nom,code,population,codeDepartement"


class CommuneError(RuntimeError):
    pass


def _get(params):
    try:
        response = requests.get(API, params=params, headers=HEADERS, timeout=20)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        raise CommuneError(f"geo.api.gouv.fr indisponible : {exc}") from exc


def search(name, limit=8):
    """Communes whose name matches, biggest first: [{code, name, department, population}]."""
    if not name.strip():
        return []
    data = _get({"nom": name, "fields": FIELDS, "boost": "population", "limit": limit})
    return [
        {"code": c["code"], "name": c["nom"], "department": c.get("codeDepartement", ""),
         "population": c.get("population")}
        for c in data
    ]


def fetch(code):
    """One commune with its boundary: {code, name, department, population, geometry}."""
    data = _get({"code": code, "fields": FIELDS + ",contour", "format": "geojson", "geometry": "contour"})
    features = data.get("features") or []
    if not features:
        raise CommuneError(f"Commune {code} introuvable")
    props = features[0]["properties"]
    return {
        "code": props["code"],
        "name": props["nom"],
        "department": props.get("codeDepartement", ""),
        "population": props.get("population"),
        "geometry": features[0]["geometry"],
    }
