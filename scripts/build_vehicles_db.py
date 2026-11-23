"""Build data/vehicles.json (EN + RU planes, helicopters, recon drones) from War Thunder datamine."""

from __future__ import annotations

import csv
import io
import json
import re
import sys
from pathlib import Path

import httpx

WPCOST_URL = "https://raw.githubusercontent.com/gszabi99/War-Thunder-Datamine/master/char.vromfs.bin_u/config/wpcost.blkx"
UNITS_CSV_URL = "https://raw.githubusercontent.com/gszabi99/War-Thunder-Datamine/master/lang.vromfs.bin_u/lang/units.csv"

PLANE_CLASSES = {"exp_fighter", "exp_bomber", "exp_assault"}
HELI_CLASSES = {"exp_helicopter"}

SEED_PLANES = [
    "Yak-130",
    "Як-130",
    "Su-22M3",
    "Су-22М3",
    "SAAB-105G",
]

SEED_HELICOPTERS = [
    "Z-11WA",
]

SEED_RECON_DRONES = [
    "Recon Micro",
    "Разведывательный БПЛА",
]

_LEADING_NATION_PREFIX_RE = re.compile(r"^[◊▄▀▂▃▅▆▇␗◌◐◩★▁▖▗▘▝◔●▲▼◆▙▛▜▟\s]+")


def normalize_vehicle_name(name: str) -> str:
    """Normalize HUD vehicle name by stripping nation symbols, collapsing spaces, and lowercasing."""
    cleaned = _LEADING_NATION_PREFIX_RE.sub("", name.strip())
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.casefold()


def parse_units_csv(csv_text: str) -> dict[str, list[str]]:
    """Map unit_id -> list of EN/RU short HUD names from units.csv."""
    reader = csv.reader(io.StringIO(csv_text), delimiter=";")
    header = next(reader, None)
    if not header:
        return {}

    col_idx: dict[str, int] = {col.strip().strip('"').lower(): idx for idx, col in enumerate(header)}
    id_col = 0
    en_col = col_idx.get("english", 1)
    ru_col = col_idx.get("russian", -1)

    unit_names: dict[str, set[str]] = {}
    for row in reader:
        if not row or len(row) <= id_col:
            continue
        key = row[id_col].strip().strip('"')
        # Short HUD/killfeed names use suffix _0, _1, _2 (and _shop for fallback)
        matched_suffix = None
        for suffix in ("_0", "_1", "_2", "_shop"):
            if key.endswith(suffix):
                matched_suffix = suffix
                break
        if not matched_suffix:
            continue

        unit_id = key[: -len(matched_suffix)]
        bucket = unit_names.setdefault(unit_id, set())
        for idx in (en_col, ru_col):
            if 0 <= idx < len(row):
                val = row[idx].strip().strip('"')
                if val and not val.startswith("unit_") and not val.startswith("us_") and "/" not in val:
                    bucket.add(val)

    return {uid: sorted(names) for uid, names in unit_names.items()}


def build_database(output_path: Path, timeout: float = 30.0) -> dict[str, list[str]]:
    """Fetch datamine files and build normalized EN+RU vehicles.json."""
    planes: set[str] = {normalize_vehicle_name(x) for x in SEED_PLANES}
    helicopters: set[str] = {normalize_vehicle_name(x) for x in SEED_HELICOPTERS}
    recon_drones: set[str] = {normalize_vehicle_name(x) for x in SEED_RECON_DRONES}

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        wp_resp = client.get(WPCOST_URL)
        wp_resp.raise_for_status()
        wpcost = wp_resp.json()

        csv_resp = client.get(UNITS_CSV_URL)
        csv_resp.raise_for_status()
        units_map = parse_units_csv(csv_resp.text)

    for unit_id, meta in wpcost.items():
        if not isinstance(meta, dict):
            continue
        unit_class = str(meta.get("unitClass", ""))
        uid_lower = unit_id.lower()
        names = units_map.get(unit_id, [])

        is_recon = (
            "recon_micro" in uid_lower
            or "uav_recon" in uid_lower
            or "micro_uav" in uid_lower
            or any("recon micro" in n.lower() for n in names)
        )
        if is_recon:
            for n in names:
                norm = normalize_vehicle_name(n)
                if norm:
                    recon_drones.add(norm)
            continue

        if unit_class in HELI_CLASSES:
            for n in names:
                norm = normalize_vehicle_name(n)
                if norm and norm not in recon_drones:
                    helicopters.add(norm)
        elif unit_class in PLANE_CLASSES:
            for n in names:
                norm = normalize_vehicle_name(n)
                if norm and norm not in recon_drones:
                    planes.add(norm)

    # Ensure recon drones are never present in planes or helicopters
    planes -= recon_drones
    helicopters -= recon_drones

    payload = {
        "planes": sorted(planes),
        "helicopters": sorted(helicopters),
        "recon_drones": sorted(recon_drones),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    out = root / "data" / "vehicles.json"
    data = build_database(out)
    print(
        f"Built {out}: {len(data['planes'])} planes, "
        f"{len(data['helicopters'])} helicopters, "
        f"{len(data['recon_drones'])} recon drones"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
