"""Chạy pipeline với nguồn vnstock giả lập (không cần mạng, không cần key SSI).

    python tests/test_vnstock_source.py
"""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


def install_fake_vnstock(prices: pd.DataFrame, uni: dict):
    by_sym = {s: g for s, g in prices.groupby("symbol")}

    class Quote:
        def __init__(self, symbol, **k):
            self.symbol = symbol.upper()

        def history(self, start=None, end=None, interval="1D", **k):
            g = by_sym.get(self.symbol)
            if g is None:
                return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
            g = g[(g["date"] >= dt.date.fromisoformat(start)) & (g["date"] <= dt.date.fromisoformat(end))]
            scale = 1 if self.symbol == "VNINDEX" else 1000  # KBS: cổ phiếu theo nghìn đồng
            return pd.DataFrame({"time": pd.to_datetime(g["date"]), "open": g["open"] / scale, "high": g["high"] / scale,
                                 "low": g["low"] / scale, "close": (g["close"] / scale).round(2), "volume": g["volume"].astype(int)})

    class Listing:
        def symbols_by_group(self, group="VN30", **k):
            return pd.Series(uni["VN30"] if group == "VN30" else uni["MID"] if group == "VNMidCap" else [], name="symbol")

    class Trading:
        def __init__(self, symbol=None, **k):
            pass

        def price_board(self, symbols_list, **k):
            last = prices[prices["date"] == prices["date"].max()].set_index("symbol")
            rows = [{"symbol": s, "foreign_buy_volume": 100000.0, "foreign_sell_volume": 40000.0 if i % 2 else 160000.0}
                    for i, s in enumerate(symbols_list) if s in last.index]
            return pd.DataFrame(rows)

    pkg = types.ModuleType("vnstock")
    sub = {}
    for name in ["vnstock.explorer", "vnstock.explorer.kbs"]:
        sub[name] = types.ModuleType(name)
    q, l, t = (types.ModuleType(f"vnstock.explorer.kbs.{n}") for n in ("quote", "listing", "trading"))
    q.Quote, l.Listing, t.Trading = Quote, Listing, Trading
    mods = {"vnstock": pkg, **sub, "vnstock.explorer.kbs.quote": q, "vnstock.explorer.kbs.listing": l, "vnstock.explorer.kbs.trading": t}
    return mock.patch.dict(sys.modules, mods)


def run_case():
    tmp = Path(tempfile.mkdtemp())
    for d in ("mcdx", "config", "template"):
        shutil.copytree(ROOT / d, tmp / d)
    sys.path.insert(0, str(tmp))
    for m in [m for m in sys.modules if m.startswith("mcdx")]:
        del sys.modules[m]
    from mcdx import run
    from mcdx.demo import demo_prices
    today = dt.date(2026, 10, 2)
    uni = json.loads((tmp / "config" / "universe_default.json").read_text())
    prices = demo_prices(uni["VN30"] + uni["MID"] + ["VNINDEX"], today)
    env = {k: v for k, v in os.environ.items() if not k.startswith("SSI_")}
    with install_fake_vnstock(prices, uni), mock.patch.dict(os.environ, env, clear=True), mock.patch("time.sleep"), \
            mock.patch.object(sys, "argv", ["run", "--today", today.isoformat()]):
        run.main()
    html = (tmp / "site" / "index.html").read_text()
    assert '"source":"vnstock (KBS) – tạm thời"' in html, "nguồn hiển thị sai"
    df = pd.read_csv(tmp / "data" / "prices.csv")
    assert df["symbol"].nunique() == len(uni["VN30"]) + len(uni["MID"]) + 1
    stock = df[df["symbol"] == uni["VN30"][0]]
    assert stock["close"].median() > 1000, "giá cổ phiếu phải lưu theo đồng"
    assert df[df["symbol"] == "VNINDEX"]["close"].median() < 5000, "VN-Index không được nhân 1000"
    f_today = df[(df["date"] == today.isoformat()) & df["fnet"].notna()]
    assert len(f_today) >= len(uni["VN30"]), "thiếu khối ngoại phiên hôm nay"
    uni_saved = json.loads((tmp / "data" / "universe.json").read_text())
    assert uni_saved["source"] == "SSI IndexComponents" or len(uni_saved["MID"]) == len(uni["MID"])
    sys.path.remove(str(tmp))
    return tmp, len(f_today)


def test_vnstock_source():
    run_case()


if __name__ == "__main__":
    tmp, nf = run_case()
    print(f"OK (vnstock giả lập): khối ngoại hôm nay {nf} mã, kết quả tại {tmp}")
