#!/usr/bin/env python3

import argparse
import csv
import json
import pathlib
import re
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = ROOT / "data" / "catalog"


def parse_number_like(value: str) -> float:
    s = (value or "").strip()
    if not s:
        return 0.0

    s = re.sub(r"\s+", "", s)
    # Normalize decimal separators: support both "1.234,56" and "1234,56"
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")

    s = re.sub(r"[^0-9.\-]", "", s)
    try:
        return float(s)
    except ValueError:
        return 0.0


def format_decimal_comma(n: float, decimals: int = 2) -> str:
    fmt = f"{{:.{decimals}f}}".format(n)
    return fmt.replace(".", ",")


def parse_weird_csv_rows(csv_path: pathlib.Path) -> Tuple[List[str], List[Dict[str, str]]]:
    """
    The input file is a CSV where the header is a normal comma-separated row,
    but each subsequent data row is itself wrapped in quotes and contains an inner
    CSV string (with doubled quotes).

    Example row:
      \"2.E,BRAND,MODEL,,EXT,INT,\"\"12,28\"\",SI,...\"
    """
    lines = csv_path.read_text("utf-8", errors="replace").splitlines()
    if not lines:
        raise ValueError("CSV vuoto")

    header = next(csv.reader([lines[0]], delimiter=","))
    if header:
        header[0] = header[0].lstrip("\ufeff")

    rows: List[Dict[str, str]] = []
    for raw in lines[1:]:
        s = raw.strip()
        if not s:
            continue
        if s.startswith('"') and s.endswith('"'):
            s = s[1:-1]
        # Unescape quotes from the outer CSV encoding.
        s = s.replace('""', '"')

        values = next(csv.reader([s], delimiter=",", quotechar='"'))
        if len(values) != len(header):
            # Skip malformed lines but keep going.
            continue
        rows.append(dict(zip(header, values)))

    return header, rows


def build_sistema_ibrido_models(rows: Iterable[Dict[str, str]]) -> List[Dict[str, Any]]:
    models: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for row in rows:
        tip = (row.get("Tipologia intervento") or "").strip()
        if tip and tip != "2.E":
            # File attuale: solo 2.E (sistemi ibridi)
            continue

        marca = (row.get("Marca") or "").strip()
        pdc_model = (row.get("Modello pompa di calore") or "").strip()
        caldaia_model = (row.get("Modello caldaia a condensazione") or "").strip()
        ext_id = (row.get("Identificativo modello unità esterna") or "").strip()
        int_id = (row.get("Identificativo modello unità interna") or "").strip()

        pdc_pot_raw = (row.get("Potenza termica Pompa di Calore [kWt]") or "").strip()
        cop_raw = (row.get("COP") or "").strip()
        caldaia_pot_raw = (row.get("Potenza termica caldaia a condensazione") or "").strip()
        caldaia_rend_raw = (row.get("Rendimento termico utile caldaia") or "").strip()
        inverter = (row.get("Presenza inverter") or "").strip()

        # Build a stable de-dup key.
        dedup_key = "|".join(
            [
                marca,
                pdc_model,
                caldaia_model,
                ext_id,
                int_id,
                pdc_pot_raw,
                cop_raw,
                caldaia_pot_raw,
                caldaia_rend_raw,
                inverter,
            ]
        ).lower()
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        label = f"{marca} {pdc_model}".strip()
        if caldaia_model:
            label = f"{label} + {caldaia_model}".strip()

        ids = []
        if ext_id:
            ids.append(ext_id)
        if int_id and int_id != ext_id:
            ids.append(int_id)
        if ids:
            label = f"{label} ({'/'.join(ids)})"

        # Approximate pdc_efficienza from COP/SCOP using the classic conversion coefficient 2.5:
        # eta_s(%) ≈ SCOP / 2.5 * 100
        cop = parse_number_like(cop_raw)
        pdc_eff = (cop / 2.5) * 100 if cop > 0 else 0.0

        # Normalize numeric strings for hidden fields (keep comma decimals).
        pdc_pot = parse_number_like(pdc_pot_raw)
        caldaia_pot = parse_number_like(caldaia_pot_raw)
        caldaia_rend = parse_number_like(caldaia_rend_raw)

        model_id = len(models) + 1

        models.append(
            {
                "id": model_id,
                "label": label,
                "fields": {
                    "id": model_id,
                    "marca": marca,
                    # The frontend copies `modello` into both `modello` and `pdc_modello` fields for sistemi ibridi.
                    "modello": label,
                    # Best-effort defaults (the CSV doesn't provide these)
                    "pdc_alimentazione": "elettrica",
                    "pdc_tipologia_scambio": "aria_acqua_bassa",
                    "pdc_denominazione": pdc_model,
                    "pdc_potenza": format_decimal_comma(pdc_pot, 2) if pdc_pot else "",
                    "pdc_efficienza": format_decimal_comma(pdc_eff, 1) if pdc_eff else "",
                    "pdc_scop_sper_cop": format_decimal_comma(cop, 2) if cop else (cop_raw or ""),
                    # Not in CSV
                    "pdc_emissione": "",
                    "pdc_potenziale": "",
                    "caldaia_tipologia": caldaia_model,
                    "caldaia_potenza": format_decimal_comma(caldaia_pot, 2) if caldaia_pot else "",
                    "caldaia_rendimento": format_decimal_comma(caldaia_rend, 1) if caldaia_rend else "",
                    "caldaia_efficienza": format_decimal_comma(caldaia_rend, 1) if caldaia_rend else "",
                },
            }
        )

    return models


def build_scaldacqua_models(rows: Iterable[Dict[str, str]]) -> List[Dict[str, Any]]:
    models: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for row in rows:
        tip = (row.get("Tipologia di intervento") or row.get("Tipologia intervento") or "").strip()
        if tip and tip != "2.D":
            # File attuale: tipologia 2.D (scaldacqua a pompa di calore)
            continue

        marca = (row.get("Marca") or "").strip()
        modello = (row.get("Modello") or "").strip()
        ext_id = (row.get("Identificativo modello unità esterna") or "").strip()
        int_id = (row.get("Identificativo modello unità interna") or "").strip()

        potenza_raw = (row.get("Potenza termica [kWt]") or "").strip()
        cop_raw = (row.get("COP") or "").strip()
        capacita_raw = (row.get("Capacità [litri]") or row.get("Capacita [litri]") or "").strip()

        dedup_key = "|".join([marca, modello, ext_id, int_id, potenza_raw, cop_raw, capacita_raw]).lower()
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        label = f"{marca} {modello}".strip()
        ids = []
        if ext_id:
            ids.append(ext_id)
        if int_id and int_id != ext_id:
            ids.append(int_id)
        if ids:
            label = f"{label} ({'/'.join(ids)})"

        try:
            capacita_int = int(float(capacita_raw.replace(",", ".") or 0))
        except ValueError:
            capacita_int = 0

        cop = parse_number_like(cop_raw)

        # CSV doesn't include energy class; infer a best-effort value from COP.
        classe_energetica = "a+" if cop >= 3.0 else "a"

        model_id = len(models) + 1
        models.append(
            {
                "id": model_id,
                "label": label,
                "fields": {
                    "id": model_id,
                    "marca": marca,
                    "modello": modello,
                    "classe_energetica": classe_energetica,
                    "capacita_accumulo": capacita_int if capacita_int else "",
                },
            }
        )

    return models


def normalize_tipologia_scambio(raw: str) -> str:
    s = (raw or "").strip().lower()
    if not s:
        return ""

    # Normalize common separators.
    s = re.sub(r"\s+", "", s)
    s = s.replace("\\", "/").replace("-", "/")

    mapping = {
        # Values used by the frontend subtype filter.
        "aria/aria": "aria_aria",
        "acqua/aria": "acqua_aria",
        "salamoia/aria": "salamoia_aria",
        # Common GSE catalog values.
        "aria/acqua": "aria_acqua_bassa",
        "acqua/acqua": "acqua_acqua",
        "salamoia/acqua": "salamoia_acqua",
    }
    if s in mapping:
        return mapping[s]

    # Fallback: slugify.
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def build_pompa_calore_models(rows: Iterable[Dict[str, str]]) -> List[Dict[str, Any]]:
    models: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for row in rows:
        tip = (row.get("Tipologia") or row.get("Tipologia intervento") or "").strip()
        if tip and tip != "2.A":
            # File attuale: tipologia 2.A (pompe di calore)
            continue

        tip_fun_raw = (row.get("Tipologia funzionamento") or "").strip()
        tip_sca_raw = (row.get("Tipologia scambio") or "").strip()
        nome_comm = (row.get("Denominazione Commerciale") or "").strip()
        marca = (row.get("Marca") or "").strip()
        modello = (row.get("Modello") or "").strip()
        ext_id = (row.get("Identificativo modello unità esterna") or "").strip()
        int_id = (row.get("Identificativo modello unità interna") or "").strip()

        potenza_raw = (row.get("Potenza termica [kWt]") or "").strip()
        inverter = (row.get("Presenza inverter") or "").strip()
        cop_raw = (row.get("COP") or "").strip()
        gue_raw = (row.get("GUE") or "").strip()
        nox_raw = (row.get("Emissioni NO2") or row.get("Emissioni NOx") or "").strip()

        # Build a stable de-dup key.
        dedup_key = "|".join(
            [
                tip,
                tip_fun_raw,
                tip_sca_raw,
                nome_comm,
                marca,
                modello,
                ext_id,
                int_id,
                potenza_raw,
                inverter,
                cop_raw,
                gue_raw,
                nox_raw,
            ]
        ).lower()
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        label = f"{marca} {modello}".strip()
        ids = []
        if ext_id:
            ids.append(ext_id)
        if int_id and int_id != ext_id:
            ids.append(int_id)
        if ids:
            label = f"{label} ({'/'.join(ids)})"

        tip_fun = tip_fun_raw.strip().lower()
        tip_sca = normalize_tipologia_scambio(tip_sca_raw)

        potenza = parse_number_like(potenza_raw)
        cop = parse_number_like(cop_raw)

        # Best-effort: treat COP as SCOP-ish and derive eta_s(%) ≈ SCOP / 2.5 * 100.
        eff = (cop / 2.5) * 100 if cop > 0 else 0.0

        model_id = len(models) + 1
        models.append(
            {
                "id": model_id,
                "label": label,
                "fields": {
                    "id": model_id,
                    "marca": marca,
                    "modello": modello,
                    "tipologia_funzionamento": tip_fun,
                    "tipologia_scambio": tip_sca or tip_sca_raw.strip().lower(),
                    "nome_commerciale": nome_comm,
                    "potenza_nominale": format_decimal_comma(potenza, 2) if potenza else "",
                    "efficienza_stagionale": format_decimal_comma(eff, 1) if eff else "",
                    "scop_sper_cop": format_decimal_comma(cop, 2) if cop else (cop_raw or ""),
                    # The GSE extract doesn't provide these fields; keep empty strings to match frontend mapping.
                    "emissione_nox": nox_raw,
                    "potenziale_gwp": "",
                },
            }
        )

    return models


def normalize_solare_tipo_collettori(raw: str) -> str:
    s = (raw or "").strip().lower()
    if not s:
        return ""

    s = re.sub(r"\s+", " ", s)
    if "factory" in s:
        return "factory_made"
    if s.startswith("piani"):
        return "piani"
    # Fallback: slug
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def normalize_solare_utilizzo(raw: str) -> str:
    s = (raw or "").strip().lower()
    if not s:
        return ""

    compact = re.sub(r"\s+", "", s)
    if "acs" in compact and "riscald" in compact:
        return "acs_riscaldamento"
    if "solo" in compact and "riscald" in compact:
        return "solo_riscaldamento"
    if "acs" in compact:
        return "solo_acs"

    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def build_solare_termico_models(rows: Iterable[Dict[str, str]]) -> List[Dict[str, Any]]:
    models: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for row in rows:
        tip = (row.get("Tipologia intervento") or row.get("Tipologia di intervento") or "").strip()
        if tip and tip != "2.C":
            # File attuale: tipologia 2.C (solare termico)
            continue

        tipo_collettori_raw = (row.get("Tipologia di collettori") or "").strip()
        utilizzo_raw = (row.get("Utilizzo") or "").strip()
        marca = (row.get("Marca") or "").strip()
        modello = (row.get("Modello") or "").strip()

        area_ag_raw = (row.get("Area AG [m2]") or "").strip()
        area_aa_raw = (row.get("Area Aa [m2]") or "").strip()

        qcol_50_raw = (row.get("Energia Qcol (50°C) [kWht/anno]") or "").strip()
        qcol_75_raw = (row.get("Energia Qcol (75°C) [kWht/anno]") or "").strip()
        qsol_50_raw = (row.get("Energia Qsol (50°C) [kWht/anno]") or "").strip()
        qsol_75_raw = (row.get("Energia Qsol (75°C) [kWht/anno]") or "").strip()
        qsol_150_raw = (row.get("Energia Qsol (150°C) [kWht/anno]") or "").strip()
        ql_mj_raw = (row.get("Energia QL [MJ/anno]") or "").strip()

        tipo_collettori = normalize_solare_tipo_collettori(tipo_collettori_raw)
        utilizzo = normalize_solare_utilizzo(utilizzo_raw)

        # Select the most useful energy metric:
        # - For factory_made, the Ariston backend expects energy in MJ/anno (QL).
        # - For piani, it expects energy in kWh/anno (prefer Qcol 50°C).
        energia_termica: float = 0.0
        if tipo_collettori == "factory_made":
            energia_termica = parse_number_like(ql_mj_raw)
        else:
            energia_termica = (
                parse_number_like(qcol_50_raw)
                or parse_number_like(qcol_75_raw)
                or parse_number_like(qsol_50_raw)
                or parse_number_like(qsol_75_raw)
                or parse_number_like(qsol_150_raw)
            )

        area_ag = parse_number_like(area_ag_raw)
        area_aa = parse_number_like(area_aa_raw)

        dedup_key = "|".join(
            [
                tip,
                tipo_collettori_raw,
                utilizzo_raw,
                marca,
                modello,
                area_ag_raw,
                area_aa_raw,
                qcol_50_raw,
                qcol_75_raw,
                qsol_50_raw,
                qsol_75_raw,
                qsol_150_raw,
                ql_mj_raw,
            ]
        ).lower()
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        label = f"{marca} {modello}".strip()
        model_id = len(models) + 1
        models.append(
            {
                "id": model_id,
                "label": label,
                "fields": {
                    "id": model_id,
                    "marca": marca,
                    "modello": modello,
                    "tipo_collettori": tipo_collettori,
                    "utilizzo": utilizzo,
                    "area_ag": format_decimal_comma(area_ag, 2) if area_ag else "",
                    "area_aa": format_decimal_comma(area_aa, 2) if area_aa else "",
                    # Keep as number-ish; backend/parser accepts both.
                    "energia_termica": energia_termica if energia_termica else "",
                },
            }
        )

    return models


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Importa un catalogo modelli da CSV e genera JSON per /api/public/catalog/{catalogType}/{brand}."
    )
    ap.add_argument("--csv", required=True, help="Path al CSV sorgente (es. catalogo_gse_final_nodup.csv)")
    ap.add_argument("--catalog-type", required=True, help='Esempio: "sistema-ibrido"')
    ap.add_argument("--brand", required=True, help='Brand/path (es. "arquati")')
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Directory base output (default: arquati-server/data/catalog)")

    args = ap.parse_args()
    csv_path = pathlib.Path(args.csv).expanduser().resolve()
    out_dir = pathlib.Path(args.out_dir).expanduser().resolve()

    if not csv_path.exists():
        print(f"CSV non trovato: {csv_path}", file=sys.stderr)
        return 2

    _header, rows = parse_weird_csv_rows(csv_path)

    if args.catalog_type == "sistema-ibrido":
        models = build_sistema_ibrido_models(rows)
    elif args.catalog_type == "scaldacqua":
        models = build_scaldacqua_models(rows)
    elif args.catalog_type == "pompa-calore":
        models = build_pompa_calore_models(rows)
    elif args.catalog_type == "solare-termico":
        models = build_solare_termico_models(rows)
    else:
        print(f"Catalog type non supportato (per ora): {args.catalog_type}", file=sys.stderr)
        return 2

    target_dir = out_dir / args.catalog_type
    target_dir.mkdir(parents=True, exist_ok=True)
    out_path = target_dir / f"{args.brand}.json"
    out_path.write_text(json.dumps(models, ensure_ascii=False), encoding="utf-8")

    print(f"Wrote {len(models)} models to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
