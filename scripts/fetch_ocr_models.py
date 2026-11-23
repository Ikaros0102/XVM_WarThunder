"""Download Cyrillic + Latin PP-OCRv5 ONNX recognition model and dictionary into data/ocr_models/."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import yaml

MODEL_URL = "https://huggingface.co/PaddlePaddle/cyrillic_PP-OCRv5_mobile_rec_onnx/resolve/main/inference.onnx"
CFG_URL = "https://huggingface.co/PaddlePaddle/cyrillic_PP-OCRv5_mobile_rec_onnx/raw/main/inference.yml"


def fetch_models(target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    model_path = target_dir / "cyrillic_rec.onnx"
    dict_path = target_dir / "cyrillic_dict.txt"

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        cfg_resp = client.get(CFG_URL)
        cfg_resp.raise_for_status()
        cfg = yaml.safe_load(cfg_resp.text)
        chars = cfg["PostProcess"]["character_dict"]
        dict_path.write_text("\n".join(str(c) for c in chars) + "\n", encoding="utf-8")

        if not model_path.exists() or model_path.stat().st_size < 100_000:
            with client.stream("GET", MODEL_URL) as resp:
                resp.raise_for_status()
                tmp = model_path.with_suffix(".tmp")
                with tmp.open("wb") as fh:
                    for chunk in resp.iter_bytes(chunk_size=65536):
                        fh.write(chunk)
                tmp.replace(model_path)

    print(f"Saved Cyrillic OCR model ({model_path.stat().st_size} bytes) and {len(chars)} dict entries.")


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    fetch_models(root / "data" / "ocr_models")
    sys.exit(0)
