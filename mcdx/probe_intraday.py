"""Thăm dò dữ liệu khớp lệnh từng giao dịch (intraday) của vnstock để tìm lệnh lớn.

    python -m mcdx.probe_intraday FPT,VIX,HPG
Ghi kết quả ra data/debug/intraday_probe.json.
"""
import inspect
import json
import sys
import time
import traceback
from pathlib import Path

OUT = Path("data/debug/intraday_probe.json")


def describe(df, n=8):
    if df is None:
        return None
    try:
        import pandas as pd
        d = {"type": type(df).__name__, "len": len(df), "columns": [str(c) for c in getattr(df, "columns", [])],
             "dtypes": {str(k): str(v) for k, v in getattr(df, "dtypes", {}).items()} if hasattr(df, "dtypes") else None,
             "head": json.loads(df.head(n).to_json(orient="records", force_ascii=False, date_format="iso")),
             "tail": json.loads(df.tail(n).to_json(orient="records", force_ascii=False, date_format="iso"))}
        for c in df.columns:
            if "vol" in str(c).lower():
                s = pd.to_numeric(df[c], errors="coerce")
                d.setdefault("vol_stats", {})[str(c)] = {"max": float(s.max()), "sum": float(s.sum()),
                                                         "ge_100k": int((s >= 100_000).sum()), "ge_10k": int((s >= 10_000).sum())}
        return d
    except Exception as e:
        return {"error": repr(e)}


def methods(cls):
    out = {}
    for name, fn in inspect.getmembers(cls, predicate=inspect.isfunction):
        if name.startswith("__"):
            continue
        try:
            out[name] = str(inspect.signature(fn))
        except Exception:
            out[name] = "?"
    return out


def main(symbols):
    res = {"symbols": symbols}
    import vnstock
    res["version"] = getattr(vnstock, "__version__", "?")
    try:
        from vnstock.explorer.kbs.quote import Quote as KQ
        from vnstock.explorer.kbs.trading import Trading as KT
        res["kbs_quote_methods"] = methods(KQ)
        res["kbs_trading_methods"] = methods(KT)
        if hasattr(KQ, "intraday"):
            res["kbs_intraday_src"] = inspect.getsource(KQ.intraday)[:4000]
    except Exception:
        res["kbs_import_error"] = traceback.format_exc()[-1500:]
    try:
        from vnstock.explorer.vci.quote import Quote as VQ
        res["vci_quote_methods"] = methods(VQ)
        if hasattr(VQ, "intraday"):
            res["vci_intraday_src"] = inspect.getsource(VQ.intraday)[:4000]
    except Exception:
        res["vci_import_error"] = traceback.format_exc()[-1500:]

    for s in symbols:
        r = res.setdefault("data", {}).setdefault(s, {})
        for src in ("kbs", "vci"):
            for ps in (100, 50000):
                key = f"{src}_ps{ps}"
                t0 = time.time()
                try:
                    if src == "kbs":
                        from vnstock.explorer.kbs.quote import Quote
                        q = Quote(s)
                    else:
                        from vnstock.explorer.vci.quote import Quote
                        q = Quote(s)
                    try:
                        df = q.intraday(page_size=ps)
                    except TypeError:
                        df = q.intraday()
                    r[key] = describe(df)
                except Exception:
                    r[key] = {"error": traceback.format_exc()[-1200:]}
                r[key + "_sec"] = round(time.time() - t0, 1)
                time.sleep(3.5)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("Đã ghi", OUT)


def compare(symbols, t_from="13:41:40", t_to="13:41:50"):
    """So sánh dữ liệu khớp lệnh KBS và VCI: số dòng, tổng KL, các dòng trong khoảng giờ, số dòng ≥ 100.000."""
    import pandas as pd
    res = {"window": [t_from, t_to], "data": {}}
    for s in symbols:
        r = res["data"].setdefault(s, {})
        # KBS: toàn bộ phiên, đủ cột
        try:
            from vnstock.explorer.kbs.quote import Quote
            t0 = time.time()
            df = Quote(s).intraday(page_size=50000, get_all=True)
            r["kbs"] = summarize_df(df, t_from, t_to)
            r["kbs"]["sec"] = round(time.time() - t0, 1)
        except Exception:
            r["kbs"] = {"error": traceback.format_exc()[-1500:]}
        time.sleep(3.5)
        # VCI: thử nhiều cỡ trang, rồi phân trang ngược theo thời gian
        from vnstock.explorer.vci.quote import Quote as VQ
        for ps in (30000, 10000, 5000, 1000):
            key = f"vci_ps{ps}"
            try:
                t0 = time.time()
                df = VQ(s).intraday(page_size=ps)
                r[key] = summarize_df(df, t_from, t_to)
                r[key]["sec"] = round(time.time() - t0, 1)
            except Exception:
                r[key] = {"error": traceback.format_exc()[-1500:]}
            time.sleep(3.5)
        # VCI phân trang bằng last_time
        try:
            frames, last, calls = [], None, 0
            t0 = time.time()
            while calls < 200:
                df = VQ(s).intraday(page_size=30000, last_time=last) if last is not None else VQ(s).intraday(page_size=30000)
                calls += 1
                if df is None or len(df) == 0:
                    break
                frames.append(df)
                tcol = "time" if "time" in df.columns else df.columns[0]
                new_last = pd.to_datetime(df[tcol]).min()
                if last is not None and new_last >= pd.to_datetime(last):
                    break
                last = new_last.strftime("%Y-%m-%d %H:%M:%S")
                time.sleep(1.2)
            if frames:
                all_df = pd.concat(frames, ignore_index=True)
                r["vci_paged"] = summarize_df(all_df, t_from, t_to)
                r["vci_paged"].update(calls=calls, sec=round(time.time() - t0, 1))
        except Exception:
            r["vci_paged"] = {"error": traceback.format_exc()[-1500:]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out = OUT.with_name("tick_compare.json")
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("Đã ghi", out)


def summarize_df(df, t_from, t_to):
    import pandas as pd
    if df is None or len(df) == 0:
        return {"len": 0}
    d = {"len": len(df), "columns": [str(c) for c in df.columns]}
    tcol = "time" if "time" in df.columns else df.columns[0]
    t = pd.to_datetime(df[tcol])
    d["min_time"], d["max_time"] = str(t.min()), str(t.max())
    vcol = next((c for c in df.columns if str(c).lower() in ("volume", "vol", "match_vol")), None)
    if vcol is not None:
        v = pd.to_numeric(df[vcol], errors="coerce")
        d["vol_sum"] = float(v.sum())
        d["ge_100k"] = int((v >= 100000).sum())
    hm = t.dt.strftime("%H:%M:%S")
    win = df[(hm >= t_from) & (hm <= t_to)]
    d["window_rows"] = json.loads(win.to_json(orient="records", force_ascii=False, date_format="iso"))
    d["head"] = json.loads(df.head(3).to_json(orient="records", force_ascii=False, date_format="iso"))
    return d


if __name__ == "__main__":
    args = [x.strip().upper() for x in (sys.argv[1] if len(sys.argv) > 1 else "VIX").split(",") if x.strip()]
    if len(sys.argv) > 2 and sys.argv[2] == "compare":
        compare(args)
    else:
        main(args)
