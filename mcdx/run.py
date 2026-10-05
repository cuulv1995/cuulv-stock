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


BASKETS = ("VN30", "MID", "SMALL")
BASKET_CODES = {"MID": ("VNMIDCAP", "VNMID", "VNMidcap"), "SMALL": ("VNSMALLCAP", "VNSML", "VNSmallCap")}


def _first(client, codes) -> list[str]:
    for code in codes:
        try:
            syms = client.index_components(code)
        except Exception:
            syms = []
        if syms:
            return syms
    return []


def refresh_universe(client, today: dt.date) -> dict:
    cur = store.read_json(store.UNIVERSE, {})
    fresh = cur.get("updated") and (today - dt.date.fromisoformat(cur["updated"])).days < 7
    if fresh and all(k in cur for k in BASKETS):
        return cur
    uni = {}
    try:
        vn30 = client.index_components("VN30")
        mid = _first(client, BASKET_CODES["MID"])
        if not mid:
            mid = [s for s in client.index_components("VN100") if s not in vn30]
        small = [s for s in _first(client, BASKET_CODES["SMALL"]) if s not in vn30 and s not in mid]
        if len(vn30) >= 25 and len(mid) >= 30:
            uni = {"VN30": vn30, "MID": mid, "SMALL": small, "updated": today.isoformat(),
                   "source": getattr(client, "universe_label", "SSI IndexComponents")}
    except Exception as e:
        log.warning("Không lấy được rổ chỉ số: %s", e)
    if not uni:
        if cur.get("VN30"):
            cur.setdefault("SMALL", [])
            return cur
        d = store.read_json(store.CONFIG / "universe_default.json", {})
        uni = {"VN30": d["VN30"], "MID": d["MID"], "SMALL": [], "updated": today.isoformat(),
               "source": "Danh sách dự phòng (ước lượng)"}
    store.write_json(store.UNIVERSE, uni)
    log.info("Rổ: VN30 %d, VNMidCap %d, VNSmallCap %d mã (%s)", len(uni["VN30"]), len(uni["MID"]), len(uni["SMALL"]), uni["source"])
    # Ngành tự động cho mã chưa có trong config/sectors.json
    if hasattr(client, "sectors"):
        auto = client.sectors()
        if auto:
            store.write_json(store.DATA / "sectors_auto.json", auto)
            log.info("Ngành tự động: %d mã", len(auto))
    return uni


def update_prices(client, uni: dict, today: dt.date, cutoff: dt.date, persist_recent: bool = True) -> pd.DataFrame:
    df = store.load_prices()
    symbols = [s for b in BASKETS for s in uni.get(b, [])] + [INDEX]

    # 1) Lịch sử cho mã mới / thiếu (DailyOhlc, chỉ chạy lần đầu hoặc khi rổ đổi)
    have = df.groupby("symbol")["date"].agg(["min", "count"]) if len(df) else pd.DataFrame(columns=["min", "count"])
    start = today - dt.timedelta(days=HISTORY_DAYS)
    missing = [s for s in symbols if s not in have.index or have.loc[s, "count"] < 200]
    for i, s in enumerate(missing, 1):
        try:
            rows = client.daily_ohlc_range(s, start, today)
            df = store.upsert_prices(df, [r for r in rows if r["date"] <= cutoff], today)
            log.info("[%d/%d] Lịch sử %s: %d phiên", i, len(missing), s, len(rows))
        except Exception as e:
            log.warning("Bỏ qua lịch sử %s: %s", s, e)
        if i % 20 == 0:
            store.save_prices(df)

    store.save_prices(df)  # lịch sử luôn được lưu

    # 2) Cập nhật 10 ngày gần nhất (kèm khối ngoại). Mã vừa tải lịch sử ở bước 1 không cần tải lại.
    recent = symbols if getattr(client, "bulk_is_cheap", False) else [s for s in symbols if s not in missing]
    last_dates = df.groupby("symbol")["date"].max().to_dict() if len(df) else {}
    rows = client.daily_bulk(recent, today - dt.timedelta(days=10), today, today=cutoff if cutoff == today else None,
                             last_dates=last_dates)
    keep = set(symbols)
    # Trước 15:00 dữ liệu hôm nay chưa chốt: bỏ qua để không lưu giá giữa phiên
    df = store.upsert_prices(df, [r for r in rows if r["symbol"] in keep and r["date"] <= cutoff], today)
    log.info("Cập nhật gần nhất: %d dòng", len(rows))
    if missing and cutoff == today and hasattr(client, "foreign_today"):
        fvol = client.foreign_today([s for s in missing if s != INDEX])
        if fvol:
            add = [{**r, "fnet": fvol[r["symbol"]] * r["close"]} for r in df[(df["date"] == today) & df["symbol"].isin(list(fvol))].to_dict("records")]
            df = store.upsert_prices(df, add, today)
    if persist_recent:
        store.save_prices(df)
    else:
        log.info("Giữa phiên: không lưu giá tạm vào kho dữ liệu.")
    return df


def pick_source(choice: str):
    """Chọn nguồn dữ liệu. auto: SSI khi có key và đăng nhập được, nếu không thì vnstock."""
    cid, secret = os.environ.get("SSI_CONSUMER_ID", ""), os.environ.get("SSI_CONSUMER_SECRET", "")
    if choice in ("auto", "ssi") and cid and secret:
        from .ssi import SSIClient
        try:
            c = SSIClient(cid, secret)
            c.authenticate()
            return c, "SSI FastConnect Data"
        except Exception as e:
            if choice == "ssi":
                raise
            log.warning("SSI lỗi (%s), chuyển sang vnstock.", e)
    elif choice == "ssi":
        raise RuntimeError("Thiếu SSI_CONSUMER_ID / SSI_CONSUMER_SECRET (đặt trong GitHub Secrets).")
    else:
        log.info("Chưa có key SSI, dùng vnstock.")
    from .vnstock_source import VnstockClient
    c = VnstockClient()
    c.authenticate()
    return c, "vnstock (KBS) – tạm thời"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--today", help="YYYY-MM-DD (thử nghiệm)")
    ap.add_argument("--source", choices=["auto", "ssi", "vnstock"], default=os.environ.get("DATA_SOURCE", "auto") or "auto",
                    help="auto: dùng SSI nếu có key, lỗi thì chuyển sang vnstock")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    now = dt.datetime.now(VN)
    today = dt.date.fromisoformat(args.today) if args.today else now.date()
    status = store.read_json(store.STATUS, {})
    intraday = False

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
        client, source = pick_source(args.source)
        uni = refresh_universe(client, today)
        mins = now.hour * 60 + now.minute
        # 11:30–15:00: chế độ giữa phiên — dùng giá phiên sáng, lần chạy 17:00 sẽ ghi đè bằng giá đóng cửa
        intraday = not args.today and 11 * 60 + 30 <= mins < 15 * 60
        cutoff = today if (mins >= 15 * 60 or args.today or intraday) else today - dt.timedelta(days=1)
        if hasattr(client, "intraday"):
            client.intraday = intraday
            client.calib_path = store.DATA / "board_calib.json"
        if intraday:
            log.info("Chế độ giữa phiên: số liệu tạm tính đến giờ chạy.")
        prices = update_prices(client, uni, today, cutoff, persist_recent=not intraday)
        uni = {b: uni[b] for b in BASKETS if uni.get(b)}

    report = compute(prices, uni, store.load_sectors(), store.load_holdings())
    latest = dt.date.fromisoformat(report["date"])
    complete = latest >= today and not intraday
    report.update({"source": source, "demo": args.demo, "generated_at": now.strftime("%H:%M %d/%m/%Y"),
                   "complete": complete, "intraday": bool(intraday and latest >= today)})
    out = build_site(report)
    log.info("Đã tạo %s (phiên %s)", out, report["date"])

    if not args.demo and not intraday:
        store.write_json(store.STATUS, {"date": today.isoformat(), "complete": complete, "latest_session": report["date"],
                                        "intraday": bool(intraday), "generated_at": now.isoformat(timespec="minutes")})
    if not args.demo and not intraday:
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
