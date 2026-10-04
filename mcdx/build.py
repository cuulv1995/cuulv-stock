"""Ghép dữ liệu JSON vào template → site/index.html."""
from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "template" / "dashboard.html"
SITE = ROOT / "site"


def _clean(o):
    if isinstance(o, float):
        return None if math.isnan(o) or math.isinf(o) else round(o, 4)
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if hasattr(o, "item"):  # numpy scalar
        return _clean(o.item())
    return o


def build_site(report: dict) -> Path:
    data = json.dumps(_clean(report), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/", data)
    SITE.mkdir(exist_ok=True)
    out = SITE / "index.html"
    out.write_text(html, encoding="utf-8")
    (SITE / ".nojekyll").write_text("")
    return out
