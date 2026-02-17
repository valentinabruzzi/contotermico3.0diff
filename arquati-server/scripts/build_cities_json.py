#!/usr/bin/env python3

import csv
import json
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCES = ROOT / "data" / "sources"
OUT_DIR = ROOT / "data" / "geo"

MAIN_CSV = SOURCES / "opendatasicilia_main.csv"
CATASTALI_CSV = SOURCES / "opendatasicilia_codici_catastali.csv"
POP_CSV = SOURCES / "opendatasicilia_popolazione_2021.csv"

OUT_JSON = OUT_DIR / "cities.json"


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


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    catastali = read_catastali()
    population = read_population()

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

    # Stable ordering for deterministic output.
    cities.sort(key=lambda c: (c.get("comune") or "").lower(),)

    OUT_JSON.write_text(json.dumps(cities, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(cities)} cities to {OUT_JSON}")


if __name__ == "__main__":
    main()

