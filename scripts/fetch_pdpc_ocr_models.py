"""Install PDPC OCR models locally; parsing itself never downloads anything.

Run after pip install -e '.[pdf,ocr]'. Model URLs and SHA-256s come from the
installed RapidOCR release's own manifest. Payloads remain under ignored data/.
"""
from __future__ import annotations

import hashlib
from importlib.util import find_spec
from pathlib import Path

import httpx
import yaml


def fetch(root: Path = Path('data/ocr-models')) -> None:
    spec = find_spec('rapidocr')
    if spec is None or spec.origin is None:
        raise RuntimeError('Install the ocr extra first')
    manifest = Path(spec.origin).parent / 'default_models.yaml'
    models = yaml.safe_load(manifest.read_text(encoding='utf-8'))['onnxruntime']['PP-OCRv4']
    selected = [('det', 'ch_PP-OCRv4_det_mobile'), ('cls', 'ch_ppocr_mobile_v2.0_cls_mobile'),
                ('rec', 'en_PP-OCRv4_rec_mobile')]
    root.mkdir(parents=True, exist_ok=True)
    with httpx.Client(follow_redirects=True, timeout=120) as client:
        for kind, name in selected:
            entry = models[kind][name]
            target = root / f'{kind}.onnx'
            if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == entry['SHA256']:
                continue
            response = client.get(entry['model_dir'])
            response.raise_for_status()
            if hashlib.sha256(response.content).hexdigest() != entry['SHA256']:
                raise ValueError(f'{name}: model checksum mismatch')
            tmp = target.with_suffix('.tmp')
            tmp.write_bytes(response.content)
            tmp.replace(target)
            print(f'{name}: verified {entry["SHA256"]}')


if __name__ == '__main__':
    fetch()
