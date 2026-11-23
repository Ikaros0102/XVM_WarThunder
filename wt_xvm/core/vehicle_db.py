"""Local vehicle classification database (planes, helicopters, recon drones)."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from wt_xvm.models import VehicleKind
from wt_xvm.paths import ensure_runtime_file

logger = logging.getLogger("wt_xvm.core.vehicle_db")

_LEADING_NATION_PREFIX_RE = re.compile(
    r"^[◊▄▀▂▃▅▆▇␗␙◌◍○◔◐◩★▁▖▗▘▝●■□▪▫◄►▲▼◆◤◥◣◢◸◹◺◿▙▛▜▟\s]+"
)

_RECON_DRONE_RE = re.compile(r"(?:recon\s+micro|разведывательн\w*\s+бпла)", re.IGNORECASE)

_HELICOPTER_RE = re.compile(
    r"^(?:"
    r"(?:ми|mi)-(?:4|8|9|17|24|28|35)(?![0-9])[a-zа-яё0-9\-]*\b|"
    r"(?:ка|ka)-(?:25|27|29|50|52)(?![0-9])[a-zа-яё0-9\-]*\b|"
    r"z-(?:9|10|11|19)(?![0-9])[a-z0-9\-]*\b|"
    r"(?:ah|uh|oh|mh|ch|sh|hh)-(?:1|6|19|34|53|58|60|64)(?![0-9])[a-z0-9\-]*\b|"
    r"(?:sa|as)[\s.\-]?(?:313|315|316|341|342|365|532)(?![0-9])[a-z0-9\-]*\b|"
    r"(?:bo|bk)[\s\-]?(?:105|117)(?![0-9])[a-z0-9\-]*\b|"
    r"ec[\s\-]?(?:665|145)(?![0-9])[a-z0-9\-]*\b|"
    r"(?:a-?109|a-?129|t-?129|hkp\s*\d+|ab\s*205|md\s*500|h-34)\b|"
    r"(?:lynx|g-lynx|wessex|wasp|scout|gazelle|alouette|tzefa|lahatut|peten|saraph)\b|"
    r"tiger\s+(?:had|hap|uht)\b"
    r")",
    re.IGNORECASE,
)

_PLANE_RE = re.compile(
    r"^(?:"
    # Exclude Soviet/Russian tank destroyers (СУ-5, СУ-57, СУ-76, СУ-85, СУ-100, СУ-122, СУ-152) via (?![0-9])
    r"(?:су|su)-(?:2|6|7|8|9|11|15|17|22|24|25|27|30|33|34|35|39)(?![0-9])[a-zа-яё0-9\-]*\b|"
    r"(?:миг|mig)-\d+[a-zа-яё0-9\-]*\b|"
    r"(?:як|yak)-\d+[a-zа-яё0-9\-]*\b|"
    r"(?:ил|il)-(?:2|4|10|28|40)(?![0-9])[a-zа-яё0-9\-]*\b|"
    r"(?:ту|tu)-(?:1|2|4|14)(?![0-9])[a-zа-яё0-9\-]*\b|"
    r"(?:пе|pe)-(?:2|3|8)(?![0-9])[a-zа-яё0-9\-]*\b|"
    r"(?:ла|la)-(?:5|7|9|11|15|174|200)(?![0-9])[a-zа-яё0-9\-]*\b|"
    r"(?:лагг|lagg)-3[a-zа-яё0-9\-]*\b|"
    r"(?:бе|be)-(?:6|12)(?![0-9])[a-zа-яё0-9\-]*\b|"
    r"(?:по|po)-2(?![0-9])[a-zа-яё0-9\-]*\b|"
    r"(?:ер|yer)-2(?![0-9])[a-zа-яё0-9\-]*\b|"
    r"(?:дб|db)-3(?![0-9])[a-zа-яё0-9\-]*\b|"
    r"(?:тб|tb)-3(?![0-9])[a-zа-яё0-9\-]*\b|"
    r"(?:ар|ar)-2(?![0-9])\b|"
    r"(?:и|i)-(?:153|15|16|29|180|185|225|301)(?![0-9])[a-zа-яё0-9\-]*\b|"
    r"(?:f/a|f|av)-\d+[a-z0-9\-]*\b|"
    r"a-(?:1h|4|5|6|7|10|11|20|26|35|36)(?![0-9])[a-z0-9\-]*\b|"
    r"b-(?:17|18|24|25|26|29|34|48|57|66)(?![0-9])[a-z0-9\-]*\b|"
    r"p-(?:26|36|38|39|40|47|51|59|61|63|400)(?![0-9])[a-z0-9\-]*\b|"
    r"(?:jas\s*39|ja\s*37|aj\s*37|ajs\s*37|j\s*35|j\s*32|j\s*29|a\s*32)[a-z0-9\-]*\b|"
    r"j-(?:7|8|10|11|15|16)(?![0-9])[a-z0-9\-]*\b|"
    r"(?:q-5|jh-7|jf-17|kf-16|ef-2000|saab-105|amx\s+a-11)\b|"
    r"(?:eurofighter|typhoon|rafale|gripen|mirage|tornado|harrier|sea\s+harrier|jaguar|kfir|nesher|buccaneer|phantom|skyhawk|starfighter|lightning|canberra|vautour|etendard|super\s+etendard|alpha\s+jet)\b"
    r")",
    re.IGNORECASE,
)

_FALLBACK_RECON_DRONES = {"recon micro", "разведывательный бпла"}
_FALLBACK_PLANES = {"yak-130", "як-130", "su-22m3", "су-22м3", "saab-105g"}
_FALLBACK_HELICOPTERS = {"z-11wa"}


def normalize_vehicle_name(name: str) -> str:
    """Normalize HUD vehicle name by stripping nation symbols, collapsing spaces, and casefolding."""
    cleaned = _LEADING_NATION_PREFIX_RE.sub("", name.strip())
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.casefold()


class VehicleDB:
    """Lookup classifier mapping HUD vehicle names to plane/helicopter/recon_drone/ground."""

    def __init__(self, json_path: Path | None = None) -> None:
        self.path = json_path or ensure_runtime_file("data/vehicles.json")
        self.planes: set[str] = set(_FALLBACK_PLANES)
        self.helicopters: set[str] = set(_FALLBACK_HELICOPTERS)
        self.recon_drones: set[str] = set(_FALLBACK_RECON_DRONES)
        self.reload()

    def reload(self) -> None:
        """Load vehicles.json safely, retaining fallback entries if unreadable."""
        if not self.path.exists():
            logger.warning("Vehicle DB %s not found; using built-in fallback entries.", self.path)
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.recon_drones = {
                normalize_vehicle_name(x) for x in data.get("recon_drones", []) if str(x).strip()
            } | _FALLBACK_RECON_DRONES
            self.helicopters = (
                {normalize_vehicle_name(x) for x in data.get("helicopters", []) if str(x).strip()}
                | _FALLBACK_HELICOPTERS
            ) - self.recon_drones
            self.planes = (
                {normalize_vehicle_name(x) for x in data.get("planes", []) if str(x).strip()}
                | _FALLBACK_PLANES
            ) - self.recon_drones
        except Exception as exc:
            logger.error("Failed to load vehicle DB %s: %s", self.path, exc)

    def classify(self, raw_name: str | None) -> VehicleKind:
        """Classify a HUD vehicle string into recon_drone, helicopter, plane, ground, or unknown."""
        if not raw_name or not raw_name.strip():
            return "unknown"

        norm = normalize_vehicle_name(raw_name)
        if not norm:
            return "unknown"

        if norm in self.recon_drones or _RECON_DRONE_RE.search(norm):
            return "recon_drone"
        if norm in self.helicopters or _HELICOPTER_RE.match(norm):
            return "helicopter"
        if norm in self.planes or _PLANE_RE.match(norm):
            return "plane"
        return "ground"
