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


if __name__ == "__main__":
    main([x.strip().upper() for x in (sys.argv[1] if len(sys.argv) > 1 else "FPT,VIX").split(",") if x.strip()])
