"""TCBS iFlash Open API (https://developers.tcbs.com.vn) — chỉ dùng các endpoint dữ liệu thị trường, chỉ đọc.

Đăng nhập: POST /gaia/v1/oauth2/openapi/token với apiKey (tạo trên TCInvest) + Smart OTP từ app TCInvest → JWT.
Không bao giờ in hoặc lưu token/API key ra log hay vào repo.

    TCBS_API_KEY=... TCBS_OTP=123456 python -m mcdx.tcbs --probe VIX,FPT
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://openapi.tcbs.com.vn"
ROOT = Path(__file__).resolve().parent.parent


class TCBSError(RuntimeError):
    pass


def _mask(value: str):
    if value and os.environ.get("GITHUB_ACTIONS"):
        print(f"::add-mask::{value}", flush=True)


class TCBSClient:
    def __init__(self, token: str | None = None, timeout: int = 20):
        self.token, self.timeout, self.calls = token, timeout, 0

    def _req(self, method: str, path: str, body: dict | None = None, auth: bool = True):
        headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "mcdx-dashboard"}
        if auth:
            headers["Authorization"] = f"Bearer {self.token}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")[:300]
            raise TCBSError(f"HTTP {e.code} {path.split('?')[0]}: {msg}") from None

    def login(self, api_key: str, otp: str) -> str:
        j = self._req("POST", "/gaia/v1/oauth2/openapi/token", {"apiKey": api_key, "otp": otp}, auth=False)
        tok = j.get("token")
        if not tok:
            raise TCBSError(f"Không nhận được token: {str(j)[:200]}")
        _mask(tok)
        self.token = tok
        return tok

    def key_expiry(self, api_key: str) -> str | None:
        try:
            return self._req("POST", "/gaia/v1/oauth2/openapi/apikey/info", {"apiKey": api_key}, auth=False).get("expireDate")
        except TCBSError as e:
            return f"lỗi: {e}"

    @staticmethod
    def token_expiry(token: str) -> dict:
        try:
            payload = token.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            out = {}
            for k in ("iat", "exp"):
                if k in claims:
                    out[k] = dt.datetime.fromtimestamp(claims[k], dt.timezone(dt.timedelta(hours=7))).isoformat()
            if "exp" in claims and "iat" in claims:
                out["hours"] = round((claims["exp"] - claims["iat"]) / 3600, 2)
            return out
        except Exception as e:
            return {"error": str(e)}

    def ticks_page(self, symbol: str, page: int, size: int = 100, head: int = -1) -> dict:
        return self._req("GET", f"/nyx/v1/intraday/{symbol}/his/paging?page={page}&size={size}&headIndex={head}")

    def ticks(self, symbol: str, max_pages: int = 2000, pause: float = 0.0) -> tuple[list[dict], dict]:
        """Toàn bộ khớp lệnh trong phiên gần nhất (mỗi trang tối đa 100 dòng)."""
        first = self.ticks_page(symbol, 0)
        rows, total, head = list(first.get("data") or []), first.get("total") or 0, first.get("headIndex", -1)
        page = 1
        while len(rows) < total and page < max_pages:
            j = self.ticks_page(symbol, page, head=head)
            d = j.get("data") or []
            if not d:
                break
            rows += d
            page += 1
            if pause:
                time.sleep(pause)
        return rows, {"total": total, "pages": page, "day": first.get("d"), "headIndex": head}

    def bars_daily(self, symbol: str, count: int = 5, kind: str = "stock") -> dict:
        to = int(time.time())
        return self._req("GET", f"/nyx/v2/stock/bars-long-term?ticker={symbol}&type={kind}&resolution=D&to={to}&countBack={count}")


def probe(symbols: list[str]):
    api_key, otp = os.environ.get("TCBS_API_KEY", ""), os.environ.get("TCBS_OTP", "")
    _mask(api_key)
    _mask(otp)
    out = {"run_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=7))).isoformat(timespec="seconds"), "symbols": {}}
    c = TCBSClient()
    try:
        t0 = time.time()
        tok = c.login(api_key, otp)
        out["login"] = {"ok": True, "sec": round(time.time() - t0, 2), "token": TCBSClient.token_expiry(tok)}
    except Exception as e:
        out["login"] = {"ok": False, "error": str(e)}
    out["api_key_expiry"] = c.key_expiry(api_key) if api_key else None
    if out["login"]["ok"]:
        for s in symbols:
            r = out["symbols"].setdefault(s, {})
            try:
                t0, n0 = time.time(), c.calls
                rows, meta = c.ticks(s)
                r.update(meta, rows=len(rows), calls=c.calls - n0, sec=round(time.time() - t0, 1))
                sides: dict = {}
                for x in rows:
                    sides[x.get("a")] = sides.get(x.get("a"), 0) + 1
                r["side_values"] = sides
                r["vol_sum"] = sum(x.get("v") or 0 for x in rows)
                r["ge_80k"] = sum(1 for x in rows if (x.get("v") or 0) >= 80000)
                r["head"], r["tail"] = rows[:5], rows[-3:]
                big = sorted(rows, key=lambda x: -(x.get("v") or 0))[:15]
                r["largest"] = big
                # các dòng trong cùng giây với giao dịch lớn nhất (xem có tách từng lần khớp không)
                if big:
                    tt = big[0].get("t")
                    r["same_second_as_largest"] = [x for x in rows if x.get("t") == tt]
            except Exception as e:
                r["error"] = str(e)
        try:
            out["bars_VIX"] = c.bars_daily("VIX", 3)
            out["bars_VNINDEX"] = c.bars_daily("VNINDEX", 3, "index")
        except Exception as e:
            out["bars_error"] = str(e)
    p = ROOT / "data" / "debug" / "tcbs_probe.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("Đăng nhập:", "thành công" if out["login"]["ok"] else out["login"].get("error"))
    print("Token:", out["login"].get("token"))
    for s, r in out["symbols"].items():
        print(s, {k: r.get(k) for k in ("rows", "total", "pages", "calls", "sec", "day", "side_values", "ge_80k", "error")})


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--probe":
        probe([x.strip().upper() for x in sys.argv[2].split(",") if x.strip()])
