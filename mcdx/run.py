"""Chạy hằng ngày: cập nhật dữ liệu SSI → tính MCDX → tạo dashboard site/index.html.

    python -m mcdx.run            # chạy thật (cần SSI_CONSUMER_ID, SSI_CONSUMER_SECRET)
    python -m mcdx.run --demo     # dữ liệu giả lập, không cần key
    python -m mcdx.run --force    # chạy lại dù hôm nay đã xong
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
from zoneinfo import ZoneInfo

import pandas as pd

from . import store
from .analysis import INDEX, compute
from .build import build_site

VN = ZoneInfo("Asia/Ho_Chi_Minh")
HISTORY_DAYS = 400
log = logging.getLogger("mcdx")


def set_output(key, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            f.write(f"{key}={value}\n")


def refresh_universe(client, today: dt.date) -> dict:
    cur = store.read_json(store.UNIVERSE, {})
    if cur.get("updated") and (today - dt.date.fromisoformat(cur["updated"])).days < 7:
        return cur
    uni = {}
    try:
        vn30 = client.index_components("VN30")
        mid = []
        for code in ("VNMIDCAP", "VNMID", "VNMidcap"):
            try:
                mid = client.index_components(code)
            except Exception:
                mid = []
            if mid:
                break
        if not mid:
            vn100 = client.index_components("VN100")
            mid = [s for s in vn100 if s not in vn30]
        if len(vn30) >= 25 and len(mid) >= 30:
            uni = {"VN30": vn30, "MID": mid, "updated": today.isoformat(), "source": "SSI IndexComponents"}
    except Exception as e:
        log.warning("Không lấy được rổ chỉ số từ SSI: %s", e)
    if not uni:
        if cur.get("VN30"):
            return cur
        d = store.read_json(store.CONFIG / "universe_default.json", {})
        uni = {"VN30": d["VN30"], "MID": d["MID"], "updated": today.isoformat(), "source": "Danh sách dự phòng (ước lượng)"}
    store.write_json(store.UNIVERSE, uni)
    log.info("Rổ: VN30 %d mã, VNMidcap %d mã (%s)", len(uni["VN30"]), len(uni["MID"]), uni["source"])
    return uni


def update_prices(client, uni: dict, today: dt.date) -> pd.DataFrame:
    df = store.load_prices()
    symbols = uni["VN30"] + uni["MID"] + [INDEX]

    # 1) Lịch sử cho mã mới / thiếu (DailyOhlc, chỉ chạy lần đầu hoặc khi rổ đổi)
    have = df.groupby("symbol")["date"].agg(["min", "count"]) if len(df) else pd.DataFrame(columns=["min", "count"])
    start = today - dt.timedelta(days=HISTORY_DAYS)
    missing = [s for s in symbols if s not in have.index or have.loc[s, "count"] < 200]
    for i, s in enumerate(missing, 1):
        try:
            rows = client.daily_ohlc_range(s, start, today)
            df = store.upsert_prices(df, rows, today)
            log.info("[%d/%d] Lịch sử %s: %d phiên", i, len(missing), s, len(rows))
        except Exception as e:
            log.warning("Bỏ qua lịch sử %s: %s", s, e)
        if i % 20 == 0:
            store.save_prices(df)

    # 2) Cập nhật 10 ngày gần nhất cho cả sàn HOSE trong 1 lần gọi (kèm khối ngoại)
    rows = client.daily_stock_price(today - dt.timedelta(days=10), today, market="HOSE")
    keep = set(symbols)
    df = store.upsert_prices(df, [r for r in rows if r["symbol"] in keep], today)
    log.info("DailyStockPrice HOSE: %d dòng", len(rows))

    # 3) VN-Index 10 ngày gần nhất
    try:
        df = store.upsert_prices(df, client.daily_ohlc(INDEX, today - dt.timedelta(days=10), today), today)
    except Exception as e:
        log.warning("Không cập nhật được VNINDEX: %s", e)
    store.save_prices(df)
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--today", help="YYYY-MM-DD (thử nghiệm)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    now = dt.datetime.now(VN)
    today = dt.date.fromisoformat(args.today) if args.today else now.date()
    status = store.read_json(store.STATUS, {})

    if args.demo:
        from .demo import demo_prices
        uni = store.read_json(store.CONFIG / "universe_default.json", {})
        uni = {"VN30": uni["VN30"], "MID": uni["MID"]}
        prices = demo_prices(uni["VN30"] + uni["MID"] + [INDEX], today)
        source = "Dữ liệu giả lập"
    else:
        if status.get("date") == today.isoformat() and status.get("complete") and not args.force:
            log.info("Hôm nay đã chạy xong, bỏ qua.")
            set_output("skip", "true")
            return
        if today.weekday() >= 5 and not args.force:
            log.info("Cuối tuần, không có phiên.")
            set_output("skip", "true")
            return
        from .ssi import SSIClient
        client = SSIClient(os.environ.get("SSI_CONSUMER_ID", ""), os.environ.get("SSI_CONSUMER_SECRET", ""))
        client.authenticate()
        uni = refresh_universe(client, today)
        prices = update_prices(client, uni, today)
        uni = {"VN30": uni["VN30"], "MID": uni["MID"]}
        source = "SSI FastConnect Data"

    report = compute(prices, uni, store.load_sectors(), store.load_holdings())
    latest = dt.date.fromisoformat(report["date"])
    complete = latest >= today
    report.update({"source": source, "demo": args.demo, "generated_at": now.strftime("%H:%M %d/%m/%Y"),
                   "complete": complete})
    out = build_site(report)
    log.info("Đã tạo %s (phiên %s)", out, report["date"])

    if not args.demo:
        store.write_json(store.STATUS, {"date": today.isoformat(), "complete": complete, "latest_session": report["date"],
                                        "generated_at": now.isoformat(timespec="minutes")})
        # lưu Top 5 và danh sách mỗi ngày để theo dõi "số phiên trong danh sách" và hiệu quả thực tế
        hist = pd.read_csv(store.SIGNALS) if store.SIGNALS.exists() else pd.DataFrame(columns=["date", "list", "symbol", "price"])
        hist = hist[hist["date"] != report["date"]]
        add = [{"date": report["date"], "list": "top5", "symbol": r["sym"], "price": r["price"]} for r in report["lists"]["top5"]]
        for b, syms in report["lists"]["new"].items():
            add += [{"date": report["date"], "list": f"new_{b}", "symbol": s, "price": report["rows"][s]["price"]} for s in syms[:10]]
        add += [{"date": report["date"], "list": "sustain", "symbol": s, "price": report["rows"][s]["price"]} for s in report["lists"]["sustain"][:10]]
        pd.concat([hist, pd.DataFrame(add)]).to_csv(store.SIGNALS, index=False)
        if not complete:
            log.warning("Chưa có dữ liệu phiên %s (mới nhất: %s). Lịch chạy sau sẽ thử lại.", today, latest)
    set_output("skip", "false")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.getLogger("mcdx").error("Lỗi: %s", e)
        sys.exit(1)
