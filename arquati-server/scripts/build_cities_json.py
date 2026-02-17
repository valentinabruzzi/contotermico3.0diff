#!/usr/bin/env python3

import csv
import math
import json
import pathlib
import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher


ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCES = ROOT / "data" / "sources"
OUT_DIR = ROOT / "data" / "geo"

MAIN_CSV = SOURCES / "opendatasicilia_main.csv"
CATASTALI_CSV = SOURCES / "opendatasicilia_codici_catastali.csv"
POP_CSV = SOURCES / "opendatasicilia_popolazione_2021.csv"
ZONES_CSV = SOURCES / "zone_climatiche_comuni_celsiuspanel_2026-02-17.csv"

OUT_JSON = OUT_DIR / "cities.json"
FRONTEND_OUT_JSON = ROOT.parent / "simulatorecontotermico-arquati.com" / "data" / "geo" / "cities.json"

VALID_ZONES = {"A", "B", "C", "D", "E", "F"}
SARDINIA_SIGLAS = {"CA", "CI", "VS", "NU", "OR", "SS", "OT", "SU"}
TOKEN_STOPWORDS = {
    "d",
    "di",
    "da",
    "de",
    "del",
    "della",
    "dello",
    "dei",
    "degli",
    "delle",
    "e",
    "ed",
    "con",
    "nel",
    "nella",
    "nello",
    "nei",
    "nelle",
    "sul",
    "sulla",
    "sullo",
    "san",
    "santa",
    "santo",
    "val",
    "terme",
    "monte",
    "borgo",
    "alto",
    "alta",
    "basso",
    "bassa",
    "umbra",
}
MANUAL_ZONE_OVERRIDES: dict[tuple[str, str], str] = {
    ("Abetone Cutigliano", "PT"): "E",
    ("Bastia Umbra", "PG"): "D",
}
PROVINCE_ALIASES = {
    "MB": ["MI"],
    "BT": ["BA"],
    "FM": ["AP"],
    "FC": ["FO"],
    "PU": ["PS"],
    "VB": ["NO"],
    "VV": ["CZ"],
    "BI": ["VC"],
    "LO": ["MI"],
    "LE": ["BR"],
    "KR": ["CZ"],
    "RN": ["FO"],
    "EN": ["CT"],
    "RI": ["AQ"],
}


def pad_istat(value: str) -> str:
    s = (value or "").strip()
    if not s:
        return ""
    # Some files use 6 digits with leading zeros; others are numeric without padding.
    return s.zfill(6)


def read_catastali() -> dict[str, str]:
    m: dict[str, str] = {}
    with CATASTALI_CSV.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            istat = pad_istat(row.get("pro_com_t", ""))
            code = (row.get("codice_catastale") or "").strip()
            if istat and code:
                m[istat] = code
    return m


def read_population() -> dict[str, str]:
    m: dict[str, str] = {}
    with POP_CSV.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            istat = pad_istat(row.get("pro_com_t", ""))
            pop = (row.get("pop_res_21") or "").strip()
            if istat and pop:
                m[istat] = pop
    return m


def normalize_name(value: str) -> str:
    s = unicodedata.normalize("NFD", str(value or ""))
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = s.replace("’", "'")
    s = s.replace("d'", "di ")
    s = s.replace("jonio", "ionio")
    s = s.replace("jonico", "ionico")
    s = s.replace("poiana", "pojana")
    s = s.replace("santo stino", "san stino")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def read_zone_rows() -> list[dict[str, str]]:
    if not ZONES_CSV.exists():
        return []

    rows: list[dict[str, str]] = []
    with ZONES_CSV.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            comune = (row.get("comune") or "").strip()
            codice_provincia = (row.get("codice_provincia") or "").strip().upper()
            zona = (row.get("zona_climatica") or "").strip().upper()
            if not comune or not codice_provincia or zona not in VALID_ZONES:
                continue
            rows.append(
                {
                    "comune": comune,
                    "codice_provincia": codice_provincia,
                    "zona_climatica": zona,
                }
            )
    return rows


def province_aliases(sigla: str) -> list[str]:
    sigla = (sigla or "").strip().upper()
    out: list[str] = []

    for p in [sigla, *PROVINCE_ALIASES.get(sigla, [])]:
        if p and p not in out:
            out.append(p)

    if sigla in SARDINIA_SIGLAS:
        for p in sorted(SARDINIA_SIGLAS):
            if p != sigla and p not in out:
                out.append(p)

    return out


def significant_tokens(name_norm: str) -> list[str]:
    return [t for t in name_norm.split() if len(t) >= 5 and t not in TOKEN_STOPWORDS]


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def resolve_city_zones(cities: list[dict], zone_rows: list[dict[str, str]]) -> tuple[dict[tuple[str, str], str], Counter, list[tuple[str, str, float, str]]]:
    pair_map: dict[tuple[str, str], set[str]] = defaultdict(set)
    name_map: dict[str, set[str]] = defaultdict(set)
    by_province: dict[str, list[tuple[str, str]]] = defaultdict(list)

    for row in zone_rows:
        comune_norm = normalize_name(row["comune"])
        provincia = row["codice_provincia"]
        zona = row["zona_climatica"]
        pair_map[(comune_norm, provincia)].add(zona)
        name_map[comune_norm].add(zona)
        by_province[provincia].append((comune_norm, zona))

    resolved: dict[tuple[str, str], str] = {}
    methods: Counter = Counter()

    unresolved: list[dict] = []
    for city in cities:
        comune = city.get("comune") or ""
        provincia = (city.get("codice_provincia") or "").upper()
        key = (comune, provincia)
        comune_norm = normalize_name(comune)

        zone: str | None = MANUAL_ZONE_OVERRIDES.get(key)
        if zone in VALID_ZONES:
            methods["manual_override"] += 1

        if zone is None:
            pair_candidates: set[str] = set()
            for alias in province_aliases(provincia):
                pair_candidates |= pair_map.get((comune_norm, alias), set())

            if len(pair_candidates) == 1:
                zone = next(iter(pair_candidates))
                methods["pair"] += 1
            elif len(pair_candidates) == 0:
                by_name = name_map.get(comune_norm, set())
                if len(by_name) == 1:
                    zone = next(iter(by_name))
                    methods["name_unique"] += 1

        if zone is None:
            fuzzy_matches: list[tuple[float, str]] = []
            for alias in province_aliases(provincia):
                for source_name, source_zone in by_province.get(alias, []):
                    score = SequenceMatcher(None, comune_norm, source_name).ratio()
                    if score >= 0.75:
                        fuzzy_matches.append((score, source_zone))

            fuzzy_matches.sort(key=lambda x: x[0], reverse=True)
            if fuzzy_matches:
                top_score = fuzzy_matches[0][0]
                second_score = fuzzy_matches[1][0] if len(fuzzy_matches) > 1 else 0.0
                top_zones = {z for s, z in fuzzy_matches if abs(s - top_score) < 1e-9}
                if len(top_zones) == 1 and top_score - second_score >= 0.06:
                    zone = next(iter(top_zones))
                    methods["fuzzy_prov"] += 1

        if zone is None:
            votes: list[str] = []
            for token in significant_tokens(comune_norm):
                token_zones: set[str] = set()
                for alias in province_aliases(provincia):
                    for source_name, source_zone in by_province.get(alias, []):
                        if token in source_name.split():
                            token_zones.add(source_zone)
                if len(token_zones) == 1:
                    votes.append(next(iter(token_zones)))

            if votes and len(set(votes)) == 1:
                zone = votes[0]
                methods["token_vote"] += 1

        if zone in VALID_ZONES:
            resolved[key] = zone
        else:
            unresolved.append(city)

    known_points: list[tuple[float, float, str, str]] = []
    for city in cities:
        comune = city.get("comune") or ""
        provincia = (city.get("codice_provincia") or "").upper()
        key = (comune, provincia)
        zone = resolved.get(key)
        lat = city.get("lat")
        lng = city.get("lng")
        if zone and isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
            known_points.append((float(lat), float(lng), provincia, zone))

    low_confidence: list[tuple[str, str, float, str]] = []
    for city in unresolved:
        comune = city.get("comune") or ""
        provincia = (city.get("codice_provincia") or "").upper()
        key = (comune, provincia)
        lat = city.get("lat")
        lng = city.get("lng")

        zone = None
        confidence = 0.0

        if isinstance(lat, (int, float)) and isinstance(lng, (int, float)) and known_points:
            candidate_points = [p for p in known_points if p[2] == provincia]

            if len(candidate_points) < 8:
                alias_set = set(province_aliases(provincia))
                candidate_points = [p for p in known_points if p[2] in alias_set]

            if len(candidate_points) < 8:
                candidate_points = known_points

            distances: list[tuple[float, str]] = []
            for p_lat, p_lng, _, p_zone in candidate_points:
                d = haversine_km(float(lat), float(lng), p_lat, p_lng)
                distances.append((d, p_zone))
            distances.sort(key=lambda x: x[0])

            top = distances[:15]
            weighted: Counter = Counter()
            for d, p_zone in top:
                weighted[p_zone] += 1.0 / max(d, 0.5)

            if weighted:
                zone, best_weight = max(weighted.items(), key=lambda kv: kv[1])
                confidence = best_weight / sum(weighted.values())
                methods["knn"] += 1

        if zone not in VALID_ZONES:
            zone = "E"
            methods["fallback_E"] += 1
            confidence = 0.0

        resolved[key] = zone
        if confidence < 0.65:
            low_confidence.append((comune, provincia, confidence, zone))

    return resolved, methods, low_confidence


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    catastali = read_catastali()
    population = read_population()
    zone_rows = read_zone_rows()

    cities: list[dict] = []
    with MAIN_CSV.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            istat = pad_istat(row.get("pro_com_t", ""))
            comune = (row.get("comune") or "").strip()
            provincia = (row.get("den_prov") or "").strip()
            codice_provincia = (row.get("sigla") or "").strip()
            regione = (row.get("den_reg") or "").strip()

            # Skip incomplete rows.
            if not istat or not comune or not codice_provincia:
                continue

            try:
                lat = float(row.get("lat") or "0")
            except ValueError:
                lat = 0.0
            try:
                lng = float(row.get("long") or "0")
            except ValueError:
                lng = 0.0

            cities.append(
                {
                    "comune": comune,
                    "codice_belfiore": catastali.get(istat, ""),
                    "zona_climatica": None,
                    "altitudine": None,
                    "gradi_giorno": None,
                    "lat": lat if lat else None,
                    "lng": lng if lng else None,
                    "abitanti": population.get(istat, ""),
                    "provincia": provincia,
                    "codice_provincia": codice_provincia,
                    "regione": regione,
                    "stato": "Italia",
                    "codice_stato": "IT",
                }
            )

    zone_map, zone_methods, low_confidence = resolve_city_zones(cities, zone_rows)
    for city in cities:
        key = (city.get("comune") or "", (city.get("codice_provincia") or "").upper())
        city["zona_climatica"] = zone_map.get(key)

    # Stable ordering for deterministic output.
    cities.sort(key=lambda c: ((c.get("comune") or "").lower(), (c.get("codice_provincia") or "").upper()))

    payload = json.dumps(cities, ensure_ascii=False)
    OUT_JSON.write_text(payload, encoding="utf-8")
    FRONTEND_OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    FRONTEND_OUT_JSON.write_text(payload, encoding="utf-8")

    print(f"Wrote {len(cities)} cities to {OUT_JSON}")
    print(f"Wrote {len(cities)} cities to {FRONTEND_OUT_JSON}")
    print(f"Zone source rows: {len(zone_rows)}")
    print(f"Zone resolution methods: {dict(zone_methods)}")
    if low_confidence:
        print(f"Low-confidence KNN assignments (<0.65): {len(low_confidence)}")


if __name__ == "__main__":
    main()
