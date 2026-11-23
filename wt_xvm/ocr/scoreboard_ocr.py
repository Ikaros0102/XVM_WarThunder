"""RapidOCR scoreboard recognizer with max-RGB preprocessing and row grouping."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image

from wt_xvm.paths import get_ocr_models_dir

logger = logging.getLogger("wt_xvm.ocr.scoreboard_ocr")

_NOISE_WORDS = {
    "игрок",
    "player",
    "очки",
    "score",
    "техника",
    "vehicle",
    "победа",
    "поражение",
    "миссия",
    "mission",
    "союзники",
    "противники",
    "allies",
    "enemies",
    "счет",
    "уничтожено",
    "помощь",
    "захваты",
    "смерти",
    "пилот",
    "наземная",
    "выбор",
    "команда",
}

_NOISE_SUBSTRINGS = (
    "очки:",
    "зоны:",
    "score:",
    "zones:",
    "время миссии",
    "mission time",
    "осталось:",
    "доступно возрожд",
    "реалистичный бой",
    "аркадный бой",
    "симуляторный бой",
    "realistic battles",
    "arcade battles",
    "simulator battles",
    "превосходство",
    "личные задания",
)

_PURE_NUMERIC_RE = re.compile(r"^[\d\s.,:/+\-()\[\]%]+$")
_LEADING_SQUAD_BADGE_RE = re.compile(r"^(?:\[\d+\]|\(\d+\)|\d{1,2})\s+(.+)$")

_CYR_TO_LAT_MAP = str.maketrans(
    {
        "А": "A",
        "В": "B",
        "Е": "E",
        "К": "K",
        "М": "M",
        "Н": "H",
        "О": "O",
        "Р": "P",
        "С": "C",
        "Т": "T",
        "У": "Y",
        "Х": "X",
        "Ё": "E",
        "І": "I",
        "а": "a",
        "в": "b",
        "е": "e",
        "к": "k",
        "м": "m",
        "н": "h",
        "о": "o",
        "р": "p",
        "с": "c",
        "т": "t",
        "у": "y",
        "х": "x",
        "ё": "e",
        "і": "i",
    }
)
_LAT_TO_CYR_MAP = str.maketrans(
    {
        "A": "А",
        "B": "В",
        "E": "Е",
        "K": "К",
        "M": "М",
        "H": "Н",
        "O": "О",
        "P": "Р",
        "C": "С",
        "T": "Т",
        "Y": "У",
        "X": "Х",
        "a": "а",
        "e": "е",
        "o": "о",
        "p": "р",
        "c": "с",
        "y": "у",
        "x": "х",
    }
)
_LAT_HOMOGLYPHS = set("ABEKMHOPCTYXabekmhopctyxIi")
_CYR_HOMOGLYPHS = set("АВЕКМНОРСТУХавекмнорстухЁёІі")


def normalize_ocr_mixed_script(text: str) -> str:
    """Fix single-script words polluted by RapidOCR Cyrillic/Latin homoglyphs (e.g. Hуpe_Maxi-L -> Hype_Maxi-L)."""
    parts = text.split(" ")
    out: list[str] = []
    for word in parts:
        lat_count = 0
        unique_lat = 0
        cyr_count = 0
        unique_cyr = 0
        for ch in word:
            if ("A" <= ch <= "Z") or ("a" <= ch <= "z"):
                lat_count += 1
                if ch not in _LAT_HOMOGLYPHS:
                    unique_lat += 1
            elif ("А" <= ch <= "я") or ch in ("Ё", "ё", "І", "і"):
                cyr_count += 1
                if ch not in _CYR_HOMOGLYPHS:
                    unique_cyr += 1

        if lat_count >= 2 and unique_cyr == 0 and cyr_count > 0:
            out.append(word.translate(_CYR_TO_LAT_MAP))
        elif unique_cyr >= 2 and unique_lat == 0 and lat_count > 0:
            out.append(word.translate(_LAT_TO_CYR_MAP))
        else:
            out.append(word)
    return " ".join(out)


def preprocess_column(img: Image.Image) -> np.ndarray:
    """Boost colored/dim scoreboard text luminance against dark backdrop and upscale small crops."""
    rgb_img = img.convert("RGB")
    if rgb_img.width < 520:
        scale = 2
        rgb_img = rgb_img.resize(
            (rgb_img.width * scale, rgb_img.height * scale),
            resample=Image.Resampling.BICUBIC,
        )
    arr = np.asarray(rgb_img, dtype=np.uint8)
    # Max across RGB channels highlights yellow/green/blue/red/white nicks uniformly
    lum = np.max(arr, axis=2)
    # Slight contrast stretch for dark backgrounds
    lum_f = np.clip((lum.astype(np.float32) - 25.0) * (255.0 / 210.0), 0.0, 255.0).astype(np.uint8)
    return np.stack([lum_f, lum_f, lum_f], axis=2)


def group_boxes_into_rows(raw_results: Iterable[list]) -> list[str]:
    """Group RapidOCR box results by Y-center into logical scoreboard rows sorted top-to-bottom."""
    items: list[tuple[float, float, float, str]] = []
    heights: list[float] = []

    for entry in raw_results or []:
        if not entry or len(entry) < 3:
            continue
        box, text, conf = entry[0], str(entry[1]).strip(), float(entry[2])
        if not text or conf < 0.40:
            continue
        xs = [pt[0] for pt in box]
        ys = [pt[1] for pt in box]
        y_center = (min(ys) + max(ys)) * 0.5
        x_left = min(xs)
        h = max(1.0, max(ys) - min(ys))
        heights.append(h)
        items.append((y_center, x_left, h, text))

    if not items:
        return []

    heights.sort()
    median_h = heights[len(heights) // 2]
    row_tol = max(8.0, median_h * 0.65)

    items.sort(key=lambda it: (it[0], it[1]))
    rows: list[list[tuple[float, float, str]]] = []
    for y_c, x_l, _h, text in items:
        if not rows or abs(y_c - rows[-1][0][0]) > row_tol:
            rows.append([(y_c, x_l, text)])
        else:
            rows[-1].append((y_c, x_l, text))

    lines: list[str] = []
    for row in rows:
        row.sort(key=lambda seg: seg[1])
        joined = " ".join(seg[2] for seg in row).strip()
        lines.append(joined)
    return lines


def filter_scoreboard_nicks(raw_lines: Iterable[str]) -> list[str]:
    """Filter header words, pure numbers, squad badges, and duplicates while preserving order."""
    seen: set[str] = set()
    cleaned: list[str] = []

    for line in raw_lines:
        text = re.sub(r"\s+", " ", line).strip()
        if len(text) < 2:
            continue
        if _PURE_NUMERIC_RE.match(text):
            continue

        # Strip leading squad index badge if present (e.g. "1 Nickname")
        squad_match = _LEADING_SQUAD_BADGE_RE.match(text)
        if squad_match and len(squad_match.group(1).strip()) >= 2:
            text = squad_match.group(1).strip()

        text = normalize_ocr_mixed_script(text)

        if len(text) < 3 and not any(0x3040 <= ord(c) <= 0x9FFF for c in text):
            continue

        lowered = text.casefold()
        if lowered in _NOISE_WORDS:
            continue
        if any(sub in lowered for sub in _NOISE_SUBSTRINGS):
            continue
        if len(text) < 2 or _PURE_NUMERIC_RE.match(text):
            continue

        if lowered not in seen:
            seen.add(lowered)
            cleaned.append(text)

    return cleaned


class ScoreboardOCR:
    """Lazy-initialized RapidOCR wrapper supporting Cyrillic + Latin nicknames."""

    def __init__(self, models_dir: Path | None = None) -> None:
        self.models_dir = models_dir or get_ocr_models_dir()
        self._engine = None

    def _ensure_engine(self) -> None:
        if self._engine is not None:
            return
        from rapidocr_onnxruntime import RapidOCR

        cyr_model = self.models_dir / "cyrillic_rec.onnx"
        cyr_dict = self.models_dir / "cyrillic_dict.txt"
        if cyr_model.exists() and cyr_dict.exists():
            logger.info("Initializing RapidOCR with bundled Cyrillic+Latin model: %s", cyr_model)
            self._engine = RapidOCR(
                rec_model_path=str(cyr_model),
                rec_keys_path=str(cyr_dict),
            )
        else:
            logger.warning("Cyrillic OCR model not found in %s; falling back to default RapidOCR.", self.models_dir)
            self._engine = RapidOCR()

    def recognize_column_detailed(self, img: Image.Image) -> tuple[list[str], dict]:
        """Run preprocessing, RapidOCR inference, row grouping, filtering, and return diagnostics."""
        self._ensure_engine()
        assert self._engine is not None
        processed = preprocess_column(img)
        raw_res, _elapsed = self._engine(processed, use_cls=False, text_score=0.40)
        raw_boxes: list[dict] = []
        for entry in raw_res or []:
            if not entry or len(entry) < 3:
                continue
            box, text, conf = entry[0], str(entry[1]).strip(), float(entry[2])
            ys = [pt[1] for pt in box] if box else [0.0]
            y_center = (min(ys) + max(ys)) * 0.5
            raw_boxes.append(
                {
                    "text": text,
                    "conf": round(conf, 3),
                    "y_center": round(y_center, 1),
                }
            )
        rows = group_boxes_into_rows(raw_res or [])
        filtered = filter_scoreboard_nicks(rows)
        diag = {
            "raw_boxes": raw_boxes,
            "grouped_rows": rows,
            "filtered": filtered,
        }
        return filtered, diag

    def recognize_column(self, img: Image.Image) -> list[str]:
        """Run preprocessing, RapidOCR inference, row grouping, and filtering on one column."""
        filtered, _diag = self.recognize_column_detailed(img)
        return filtered

    def recognize_columns(self, left_img: Image.Image, right_img: Image.Image) -> tuple[list[str], list[str]]:
        """Recognize ally (left) and enemy (right) nickname columns."""
        return self.recognize_column(left_img), self.recognize_column(right_img)
