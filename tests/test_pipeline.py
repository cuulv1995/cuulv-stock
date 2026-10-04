"""Chạy toàn bộ pipeline với một SSI giả lập (không cần mạng, không cần key thật).

    python -m pytest -q tests        hoặc        python tests/test_pipeline.py
"""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


class FakeResp:
    def __init__(self, payload, code=200):
        self._p, self.status_code = payload, code

    def json(self):
        return self._p


def make_fake(prices, require_style):
    by_sym = {s: g for s, g in prices.groupby("symbol")}
    calls = {"get": 0}

    def fmt(d):
        return d.strftime("%d/%m/%Y")

    def post(url, json=None, timeout=None):
        if "/v2.0/Market/AccessToken" not in url:
            return FakeResp({"status": 404, "message": "Not found"}, 404)
        if json.get("consumerID") == "id" and json.get("consumerSecret") == "secret":
            return FakeResp({"status": 200, "message": "Success", "data": {"accessToken": "tok"}})
        return FakeResp({"status": 400, "message": "Invalid consumer"})

    def get(url, params=None, timeout=None, headers=None):
        calls["get"] += 1
        assert headers["Authorization"] == "Bearer tok"
        is_lookup = all(k.startswith("lookupRequest.") for k in params)
        if (require_style == "plain") == is_lookup:
            return FakeResp({"status": 400, "message": "Bad request", "data": None})
        p = {k.replace("lookupRequest.", ""): v for k, v in params.items()}
        path = url.rsplit("/", 1)[-1]
        if path == "IndexComponents":
            code = p["indexCode"]
            uni = json.loads((ROOT / "config" / "universe_default.json").read_text())
            syms = uni["VN30"] if code == "VN30" else (uni["MID"] if code == "VNMIDCAP" else [])
            if not syms:
                return FakeResp({"status": 200, "data": []})
            return FakeResp({"status": 200, "data": [{"IndexCode": code, "IndexComponent": [{"StockSymbol": s} for s in syms]}]})
        frm = dt.datetime.strptime(p["fromDate"], "%d/%m/%Y").date()
        to = dt.datetime.strptime(p["toDate"], "%d/%m/%Y").date()
        page, size = int(p["pageIndex"]), int(p["pageSize"])
        if path == "DailyOhlc":
            g = by_sym.get(p["symbol"])
            rows = [] if g is None else [
                {"Symbol": r.symbol, "TradingDate": fmt(r.date), "Open": str(r.open), "High": str(r.high), "Low": str(r.low),
                 "Close": str(r.close), "Volume": str(int(r.volume)), "Value": str(r.value)}
                for r in g.itertuples() if frm <= r.date <= to]
        elif path == "DailyStockPrice":
            rows = [{"Symbol": r.symbol, "TradingDate": fmt(r.date), "OpenPrice": r.open, "HighestPrice": r.high,
                     "LowestPrice": r.low, "ClosePrice": r.close, "TotalMatchVol": r.volume, "TotalMatchVal": r.value,
                     "ForeignBuyValTotal": max(r.fnet, 0), "ForeignSellValTotal": max(-r.fnet, 0)}
                    for r in prices.itertuples() if frm <= r.date <= to and r.symbol != "VNINDEX"]
        else:
            return FakeResp({"status": 404, "data": None}, 404)
        chunk = rows[(page - 1) * size: page * size]
        return FakeResp({"status": 200, "message": "Success", "totalRecord": len(rows), "data": chunk})

    return post, get, calls


def run_case(require_style):
    tmp = Path(tempfile.mkdtemp())
    for d in ("mcdx", "config", "template"):
        shutil.copytree(ROOT / d, tmp / d)
    sys.path.insert(0, str(tmp))
    for m in [m for m in sys.modules if m.startswith("mcdx")]:
        del sys.modules[m]
    from mcdx import run, ssi  # noqa
    from mcdx.demo import demo_prices
    ssi.SSIClient.pause = 0
    today = dt.date(2026, 10, 2)
    uni = json.loads((tmp / "config" / "universe_default.json").read_text())
    prices = demo_prices(uni["VN30"] + uni["MID"] + ["VNINDEX"], today)
    post, get, calls = make_fake(prices, require_style)
    env = {"SSI_CONSUMER_ID": "id", "SSI_CONSUMER_SECRET": "secret"}
    with mock.patch.dict(os.environ, env), mock.patch("requests.Session.post", side_effect=post), \
            mock.patch("requests.Session.get", side_effect=get), mock.patch("time.sleep"), \
            mock.patch.object(sys, "argv", ["run", "--today", today.isoformat()]):
        run.main()
    status = json.loads((tmp / "data" / "status.json").read_text())
    html = (tmp / "site" / "index.html").read_text()
    assert status["complete"] and status["latest_session"] == "2026-10-02", status
    assert '"source":"SSI FastConnect Data"' in html
    import pandas as pd
    df = pd.read_csv(tmp / "data" / "prices.csv")
    assert df["symbol"].nunique() == len(uni["VN30"]) + len(uni["MID"]) + 1
    assert df["fnet"].notna().sum() > 0
    sys.path.remove(str(tmp))
    # chạy lần 2 cùng ngày phải bỏ qua
    return calls["get"], tmp


def test_lookup_style():
    n, _ = run_case("lookup")
    assert n > 0


def test_plain_style_fallback():
    n, _ = run_case("plain")
    assert n > 0


if __name__ == "__main__":
    for style in ("lookup", "plain"):
        n, tmp = run_case(style)
        print(f"OK ({style}): {n} lần gọi GET, kết quả tại {tmp}")
