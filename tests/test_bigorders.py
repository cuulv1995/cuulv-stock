"""Kiểm tra tổng hợp lệnh khớp lớn và lịch sử theo phiên.

    python tests/test_bigorders.py
"""
import datetime as dt
import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcdx import bigorders, store  # noqa: E402


def ticks(day):
    t = pd.Timestamp(f"{day} 09:30:00")
    return pd.DataFrame({
        "time": [t, t + pd.Timedelta(minutes=1), t + pd.Timedelta(minutes=2), t + pd.Timedelta(minutes=3), t + pd.Timedelta(hours=5)],
        "price": [20000, 20100, 19900, 20000, 20050],
        "volume": [150000, 99999, 200000, 1000, 500000],
        "side": ["buy", "buy", "sell", "sell", "atc"],
    })


def test_summarize():
    r = bigorders.summarize("AAA", ticks("2026-11-06"))
    assert (r["buy_n"], r["sell_n"]) == (1, 1), r          # 99.999 cổ và ATC không tính
    assert r["buy_vol"] == 150000 and r["sell_vol"] == 200000
    assert r["net_vol"] == -50000 and r["net_val"] == round(3.0 - 3.98, 2)
    assert r["share"] == round(350000 / 950999 * 100, 1)
    assert [t["side"] for t in r["trades"]] == ["buy", "sell"]


class FakeClient:
    def ticks(self, s):
        if s == "ERR":
            raise RuntimeError("lỗi mạng")
        if s == "OLD":
            return ticks("2026-11-05")
        return ticks("2026-11-06")


def test_scan_and_history():
    tmp = Path(tempfile.mkdtemp())
    bigorders.OUT = tmp
    for i in range(12):  # chỉ giữ KEEP_DAYS phiên
        store.write_json(tmp / f"2026-10-{i + 1:02d}.json", {"date": f"2026-10-{i + 1:02d}", "by_sym": {}, "scanned_syms": []})
    res = bigorders.scan(FakeClient(), ["AAA", "ERR", "OLD", "BBB"], 5, dt.date(2026, 11, 6))
    assert set(res["by_sym"]) == {"AAA", "BBB"} and res["scanned"] == 3, res   # OLD là phiên cũ, bỏ qua
    assert len(list(tmp.glob("20*.json"))) == bigorders.KEEP_DAYS
    hist = bigorders.load_hist(3)
    assert [h["date"] for h in hist] == ["2026-11-06", "2026-10-12", "2026-10-11"]
    h = bigorders.symbol_history(hist, "AAA")
    assert h[0]["scanned"] and h[0]["buy_n"] == 1 and h[1] == {"date": "2026-10-12", "scanned": False}
    assert bigorders.symbol_history(hist, "OLD")[0]["buy_n"] == 0   # đã quét nhưng không có lệnh lớn


if __name__ == "__main__":
    test_summarize()
    test_scan_and_history()
    print("OK (lệnh lớn)")
