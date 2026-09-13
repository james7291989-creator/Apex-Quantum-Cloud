# -*- coding: utf-8 -*-
"""
property_engine.py :: APEX SV-1500 ArcGIS REST Data Siphon (zero-cost, public)
-------------------------------------------------------------------------------
Pure synchronous HTTP GET calls (requests) against OPEN municipal ArcGIS REST
MapServer / FeatureServer endpoints. No commercial APIs, no DOM scraping, no
headless browsers. Designed to drop into the existing Flask/sync app.py
architecture (no event loops, no async).

Field schemas VERIFIED against live services (2026-09):
  * St. Louis County, MO
      service : maps.stlouisco.com/hosting/rest/services/Maps/AGS_Parcels
      layer   : 0 (Parcels)  |  where field: PROP_ADD
      sqft    : RESQFT       |  year built: YEARBLT
  * Jefferson County, MO
      service : services1.arcgis.com/Ur3TPhgM56qvxaar ... Tax_Parcels
      layer   : 2 (Tax_Parcels)  |  where field: Situs
      NOTE    : no public sqft / year-built field exists in open schema.
                Engine returns what exists (identity, ownership, occupancy)
                and None specs so callers fall back to manual entry.

Primary contract:
    get_property_data_sync(address, county_hint=None, zip_code=None, timeout=8)
        -> {"sqft": int|None, "year_built": int|None, "county": str|None,
            "parcel_id": str|None, "owner": str|None, "situs": str|None,
            "data_source": str, "raw": dict}
"""
import re
import requests

UA = {"User-Agent": "SV1500-ApexDataSiphon/1.0 (public ArcGIS REST)"}
DEFAULT_TIMEOUT = 8  # seconds - ArcGIS REST can lag under load

# ---------------------------------------------------------------------------
# COUNTY REGISTRY :: verified service URLs + live field aliases
# ---------------------------------------------------------------------------
COUNTY_REGISTRY = {
    "STLOUIS_CNTY_MO": {
        "name": "St. Louis County, MO",
        "service_url": "https://maps.stlouisco.com/hosting/rest/services/Maps/AGS_Parcels/MapServer",
        "layer_id": 0,
        "where_field": "PROP_ADD",
        "situs_field": "PROP_ADD",
        "supports_specs": True,
        "fields": {
            "sqft": "RESQFT",
            "year_built": "YEARBLT",
            "owner": "OWNER_NAME",
            "zip": "PROP_ZIP",
            "tenure": "TENURE",
            "total_appraised_value": "TOTAPVAL",
            "improvement_value": "APPIMPVAL",
            "deed_type": "DEEDTYPE",
            "municipality": "MUNICIPALITY",
        },
        "notes": "Verified live: RESQFT/YEARBLT populate for residential parcels.",
    },
    "JEFFERSON_CNTY_MO": {
        "name": "Jefferson County, MO",
        "service_url": "https://services1.arcgis.com/Ur3TPhgM56qvxaar/arcgis/rest/services/Tax_Parcels/FeatureServer",
        "layer_id": 2,
        "where_field": "Situs",
        "situs_field": "Situs",
        "supports_specs": False,
        "fields": {
            "owner": "owner_name",
            "occupancy": "occupancy",
            "acres": "calc_acres",
            "parcel_id": "ParcelID",
            "prop_class": "prop_class",
            "municipality": "CityDesc",
            "deed_date": "deed_date",
        },
        "notes": "Public schema exposes NO sqft / year-built fields; identity/ownership only.",
    },
}
COUNTY_HINTS = {
    "st. louis": "STLOUIS_CNTY_MO",
    "stlouis": "STLOUIS_CNTY_MO",
    "jefferson": "JEFFERSON_CNTY_MO",
}

# Advisory allow-list of unambiguous ZIPs (not authoritative county boundaries)
ZIP_COUNTY = {
    "63031": "STLOUIS_CNTY_MO",  # Florissant
    "63033": "STLOUIS_CNTY_MO",  # Florissant
    "63105": "STLOUIS_CNTY_MO",  # Clayton
    "63119": "STLOUIS_CNTY_MO",  # Webster Groves
    "63122": "STLOUIS_CNTY_MO",  # Crestwood
    "63028": "JEFFERSON_CNTY_MO",  # Festus
    "63020": "JEFFERSON_CNTY_MO",  # Crystal City
    "63050": "JEFFERSON_CNTY_MO",  # Hillsboro / De Soto strip
    "63015": "JEFFERSON_CNTY_MO",  # De Soto
}

_STREET_NOISE = {"RD", "ST", "AVE", "LN", "DR", "BLVD", "CT", "MO", "MISSOURI",
                 "HWY", "PKWY", "NE", "SE", "SW", "NW", "N", "S", "E", "W"}


def _safe_int(value):
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def parse_address(raw):
    """Returns (house_number, street_tokens, zip_code)."""
    text = re.sub(r"\s+", " ", str(raw or "")).strip().upper()
    zip_match = re.search(r"\b\d{5}\b", text)
    zip_code = zip_match.group(0) if zip_match else ""

    num_match = re.search(r"^(\d+)", text)
    house_num = num_match.group(1) if num_match else ""

    rest = re.sub(r"^\d+", "", text).strip()
    if zip_code:
        rest = rest.replace(zip_code, "")
    tokens = [t for t in re.split(r"[,\s]+", rest)
              if t and len(t) > 1 and t not in _STREET_NOISE]
    return house_num, tokens[:3], zip_code


def resolve_county(address, zip_code, county_hint=None):
    """County resolution: explicit hint -> address text -> ZIP allow-list."""
    if county_hint:
        low = str(county_hint).lower()
        for key, code in COUNTY_HINTS.items():
            if key in low:
                return code
    text = str(address or "").upper()
    for key, code in COUNTY_HINTS.items():
        if key in text:
            return code
    if zip_code and str(zip_code) in ZIP_COUNTY:
        return ZIP_COUNTY[str(zip_code)]
    return None


def _build_where(entry, house_num, tokens):
    """ANTI-SUBSTRING: house number as prefix pattern, street tokens padded."""
    field = entry["where_field"]
    clauses = []
    if house_num:
        clauses.append("UPPER({0}) LIKE '{1}%'".format(field, house_num))
    for tok in tokens:
        safe = tok.replace("'", "''")
        clauses.append("UPPER({0}) LIKE '% {1} %'".format(field, safe))
    return " AND ".join(clauses)


def _query_layer(entry, where, out_fields, timeout=DEFAULT_TIMEOUT):
    url = "{0}/{1}/query".format(entry["service_url"].rstrip("/"), entry["layer_id"])
    params = {
        "f": "json",
        "where": where,
        "outFields": ",".join(out_fields),
        "returnGeometry": "false",
        "resultRecordCount": "5",
    }
    try:
        resp = requests.get(url, params=params, timeout=timeout, headers=UA)
        if resp.status_code != 200:
            return []
        data = resp.json()
        return (data or {}).get("features") or []
    except Exception:
        return []
def _score(attrs, entry, house_num, tokens):
    situs = str(attrs.get(entry["situs_field"]) or "").upper()
    score = 0
    if house_num and situs.lstrip().startswith(house_num):
        score += 100
    padded = " " + situs + " "
    for t in tokens:
        if (" " + t + " ") in padded:
            score += 20
    sq = attrs.get(entry["fields"].get("sqft")) if entry["fields"].get("sqft") else None
    if _safe_int(sq) not in (None, 0):
        score += 5
    return score


def _normalize(entry, attrs, county_key, address):
    """Normalize raw ArcGIS attributes into the SV-1500 schema dict."""
    out = {
        "address_input": str(address),
        "county": county_key,
        "data_source": "ARCGIS:" + county_key,
        "sqft": None,
        "year_built": None,
        "parcel_id": None,
        "owner": None,
        "situs": attrs.get(entry["situs_field"]),
        "raw": dict(attrs or {}),
    }
    fmap = entry["fields"]

    sq_val = attrs.get(fmap["sqft"]) if fmap.get("sqft") else None
    sq = _safe_int(sq_val)
    out["sqft"] = sq if (sq or 0) > 0 else None

    yb_val = attrs.get(fmap["year_built"]) if fmap.get("year_built") else None
    yb = _safe_int(yb_val)
    out["year_built"] = yb if (yb or 0) > 0 else None

    for slot in ("parcel_id", "owner", "occupancy", "tenure", "acres",
                 "prop_class", "municipality", "deed_date", "deed_type",
                 "total_appraised_value", "improvement_value"):
        src = fmap.get(slot)
        if src and attrs.get(src) is not None:
            out[slot] = attrs.get(src)
    return out


def get_property_data_sync(address, county_hint=None, zip_code=None, timeout=DEFAULT_TIMEOUT):
    """Primary entry point. Always returns a dict; never raises."""
    empty = {"sqft": None, "year_built": None, "county": None, "parcel_id": None,
             "owner": None, "situs": None, "data_source": "ARCGIS:NONE", "raw": {}}
    try:
        house_num, tokens, parsed_zip = parse_address(address)
        zip_code = zip_code or parsed_zip or ""
        county_key = resolve_county(address, zip_code, county_hint)
        if not county_key:
            return empty
        entry = COUNTY_REGISTRY[county_key]

        where = _build_where(entry, house_num, tokens)
        if not where:
            return empty

        fields = set()
        fields.add(entry["where_field"])
        fields.add(entry["situs_field"])
        fields.update(entry["fields"].values())

        features = _query_layer(entry, where, sorted(fields), timeout)
        if not features:
            return empty

        best = max(features, key=lambda f: _score(f.get("attributes") or {},
                                                  entry, house_num, tokens))
        attrs = best.get("attributes") or {}
        out = _normalize(entry, attrs, county_key, str(address))

        # ZERO-ERROR guard: no credible house-number anchor -> no foreign specs
        situs = str(attrs.get(entry["situs_field"]) or "").upper().lstrip()
        if not (house_num and situs.startswith(house_num)):
            out["sqft"] = None
            out["year_built"] = None

        return out
    except Exception:
        return empty