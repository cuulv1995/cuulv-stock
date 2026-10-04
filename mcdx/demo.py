"""Sinh dữ liệu giá giả lập để chạy thử toàn bộ pipeline khi chưa có key SSI."""
from __future__ import annotations

import datetime as dt
import zlib

import numpy as np
import pandas as pd


def demo_prices(symbols: list[str], today: dt.date, n: int = 300) -> pd.DataFrame:
    days = pd.bdate_range(end=today, periods=n).date
    regimes = ["new", "new", "sustain", "flat", "flat", "down", "late", "sustain"]
    rows = []
    for j, s in enumerate(symbols):
        rng = np.random.default_rng(zlib.crc32(s.encode()))
        reg = "index" if s == "VNINDEX" else regimes[j % len(regimes)]
        p = 1250.0 if reg == "index" else rng.uniform(10, 120) * 1000
        vol0 = rng.uniform(0.5, 6) * 1e6
        for i, d in enumerate(days):
            left = n - 1 - i
            mu, sd = 0.0003, 0.016
            if reg == "new":
                mu, sd = (0.022, 0.008) if left < 4 else ((-0.003, 0.011) if left < 40 else (mu, sd))
            elif reg == "sustain":
                mu, sd = (0.0055, 0.009) if left < 28 else ((-0.001, 0.012) if left < 70 else (mu, sd))
            elif reg == "late":
                mu, sd = (0.014, 0.008) if left < 9 else ((-0.003, sd) if left < 45 else (mu, sd))
            elif reg == "down" and left < 30:
                mu, sd = -0.006, 0.013
            elif reg == "index":
                mu, sd = (0.008, 0.006) if left < 5 else (0.0002, 0.009)
            ret = mu + sd * rng.standard_normal()
            o = p
            p = max(1.0, p * (1 + ret))
            vol = vol0 * rng.uniform(0.6, 1.4) * (1 + ret * 28 if ret > 0.01 else 1)
            rows.append({"date": d, "symbol": s, "open": o, "high": max(o, p) * 1.006, "low": min(o, p) * 0.994,
                         "close": round(p, 2), "volume": round(vol, -2), "value": p * vol,
                         "fnet": (ret * 40 + rng.standard_normal() * 0.6) * vol * p / 40 / 1e1})
    return pd.DataFrame(rows)
