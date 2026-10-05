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


CALLS = {"history": 0, "board": 0}
SMALL = ["BFC", "DPR", "PAN", "TNG", "NTL"]


def install_fake_vnstock(prices: pd.DataFrame, uni: dict, board_day=None):
    by_sym = {s: g for s, g in prices.groupby("symbol")}

    class Quote:
        def __init__(self, symbol, **k):
            self.symbol = symbol.upper()

        def history(self, start=None, end=None, interval="1D", **k):
            CALLS["history"] += 1
            g = by_sym.get(self.symbol)
            if g is None:
                return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
            g = g[(g["date"] >= dt.date.fromisoformat(start)) & (g["date"] <= dt.date.fromisoformat(end))]
            scale = 1 if self.symbol == "VNINDEX" else 1000  # KBS: cổ phiếu theo nghìn đồng
            return pd.DataFrame({"time": pd.to_datetime(g["date"]), "open": g["open"] / scale, "high": g["high"] / scale,
                                 "low": g["low"] / scale, "close": (g["close"] / scale).round(2), "volume": g["volume"].astype(int)})

    class Listing:
        def symbols_by_group(self, group="VN30", **k):
            m = {"VN30": uni["VN30"], "VNMidCap": uni["MID"], "VNSmallCap": SMALL}
            return pd.Series(m.get(group, []), name="symbol")

        def symbols_by_industries(self, lang="vi", **k):
            return pd.DataFrame({"symbol": SMALL, "industry_code": "X", "industry_name": "Ngành tự động"})

    class Trading:
        def __init__(self, symbol=None, **k):
            pass

        def price_board(self, symbols_list, get_all=False, **k):
            CALLS["board"] += 1
            day = board_day or prices["date"].max()
            last = prices[prices["date"] == day].set_index("symbol")
            # Bảng giá: giá theo đồng, khối lượng theo đơn vị 10 cổ phiếu (để kiểm tra hiệu chỉnh)
            rows = [{"symbol": s, "open_price": last.loc[s, "open"], "high_price": last.loc[s, "high"],
                     "low_price": last.loc[s, "low"], "close_price": round(last.loc[s, "close"] / 1000, 2) * 1000,
                     "volume_accumulated": last.loc[s, "volume"] / 10, "total_value": last.loc[s, "value"],
                     "foreign_buy_volume": 10000.0, "foreign_sell_volume": 4000.0 if i % 2 else 16000.0}
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
    day1, day2 = dt.date(2026, 10, 1), dt.date(2026, 10, 2)
    uni = json.loads((tmp / "config" / "universe_default.json").read_text())
    prices = demo_prices(uni["VN30"] + uni["MID"] + SMALL + ["VNINDEX"], day2)
    env = {k: v for k, v in os.environ.items() if not k.startswith("SSI_")}
    with mock.patch.dict(os.environ, env, clear=True), mock.patch("time.sleep"):
        # Ngày 1: tải lịch sử
        p1 = prices[prices["date"] <= day1]
        with install_fake_vnstock(p1, uni), mock.patch.object(sys, "argv", ["run", "--today", day1.isoformat()]):
            run.main()
        # Ngày 2: chỉ cập nhật qua bảng giá
        CALLS.update(history=0, board=0)
        with install_fake_vnstock(prices, uni, board_day=day2), mock.patch.object(sys, "argv", ["run", "--today", day2.isoformat()]):
            run.main()
    html = (tmp / "site" / "index.html").read_text()
    assert '"source":"vnstock (KBS) – tạm thời"' in html, "nguồn hiển thị sai"
    df = pd.read_csv(tmp / "data" / "prices.csv")
    n_sym = len(uni["VN30"]) + len(uni["MID"]) + len(SMALL) + 1
    assert df["symbol"].nunique() == n_sym, df["symbol"].nunique()
    d2 = df[df["date"] == day2.isoformat()].set_index("symbol")
    assert len(d2) == n_sym, f"thiếu phiên ngày 2: {len(d2)}/{n_sym}"
    truth = prices[prices["date"] == day2].set_index("symbol")
    s0 = uni["VN30"][0]
    assert abs(d2.loc[s0, "close"] / truth.loc[s0, "close"] - 1) < 0.01, "giá bảng giá quy đổi sai"
    assert abs(d2.loc[s0, "volume"] / truth.loc[s0, "volume"] - 1) < 0.01, "khối lượng bảng giá quy đổi sai"
    assert d2["fnet"].notna().sum() >= n_sym - 1, "thiếu khối ngoại ngày 2"
    assert df[df["symbol"] == "VNINDEX"]["close"].median() < 5000, "VN-Index không được nhân 1000"
    assert CALLS["history"] <= 5, f"ngày 2 gọi lịch sử quá nhiều: {CALLS}"
    uni_saved = json.loads((tmp / "data" / "universe.json").read_text())
    assert sorted(uni_saved["SMALL"]) == sorted(SMALL), uni_saved["SMALL"]
    sectors = json.loads((tmp / "data" / "sectors_auto.json").read_text())
    assert sectors.get("BFC") == "Ngành tự động"
    assert '"SMALL"' in html
    sys.path.remove(str(tmp))
    return tmp, dict(CALLS)


def test_vnstock_source():
    run_case()


if __name__ == "__main__":
    tmp, calls = run_case()
    print(f"OK (vnstock giả lập): ngày 2 dùng {calls['history']} lần gọi lịch sử + {calls['board']} lần gọi bảng giá, kết quả tại {tmp}")
