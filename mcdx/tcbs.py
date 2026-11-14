"""TCBS iFlash Open API (https://developers.tcbs.com.vn) — chỉ dùng các endpoint dữ liệu thị trường, chỉ đọc.

Đăng nhập: POST /gaia/v1/oauth2/openapi/token với apiKey (tạo trên TCInvest) + Smart OTP từ app TCInvest → JWT.
Không bao giờ in hoặc lưu token/API key ra log hay vào repo.

    TCBS_API_KEY=... TCBS_OTP=123456 python -m mcdx.tcbs --probe VIX,FPT
    TCBS_API_KEY=... TCBS_OTP=123456 python -m mcdx.tcbs --login     # lưu token (mã hóa) vào data/tcbs_token.enc

Token sống 24 giờ. Token được mã hóa AES-256 bằng chính API key (bí mật trong GitHub Secrets) trước khi lưu
vào repo, nên chỉ máy chạy có API key mới đọc được.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://openapi.tcbs.com.vn"
ROOT = Path(__file__).resolve().parent.parent
TOKEN_FILE = ROOT / "data" / "tcbs_token.enc"
VN = dt.timezone(dt.timedelta(hours=7))
MAX_RPS = 8.0  # TCBS khuyến nghị dưới 10 request/giây


class TCBSError(RuntimeError):
    pass


def _mask(value: str):
    if value and os.environ.get("GITHUB_ACTIONS"):
        print(f"::add-mask::{value}", flush=True)


class _Limiter:
    """Giới hạn tốc độ chung cho mọi luồng."""
    def __init__(self, rps: float):
        self.gap, self.next, self.lock = 1.0 / rps, 0.0, threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next)
            self.next = t + self.gap
        if t > now:
            time.sleep(t - now)


class TCBSClient:
    tick_source = "TCBS"

    def __init__(self, token: str | None = None, timeout: int = 20, rps: float = MAX_RPS):
        self.token, self.timeout, self.calls = token, timeout, 0
        self.limiter = _Limiter(rps)

    def _req(self, method: str, path: str, body: dict | None = None, auth: bool = True, retries: int = 3):
        last = None
        for i in range(retries):
            self.limiter.wait()
            try:
                return self._req1(method, path, body, auth)
            except TCBSError as e:
                last = e
                if " 401 " in f" {e} " or "HTTP 400" in str(e) or "HTTP 403" in str(e):
                    raise
                time.sleep(1.5 * (i + 1))
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last = TCBSError(str(e))
                time.sleep(1.5 * (i + 1))
        raise last

    def _req1(self, method: str, path: str, body: dict | None = None, auth: bool = True):
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
        j = self._req("POST", "/gaia/v1/oauth2/openapi/token", {"apiKey": api_key, "otp": otp}, auth=False, retries=1)
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

    def tick_frame(self, symbol: str):
        """Khớp lệnh phiên gần nhất dạng DataFrame cho bigorders: time, price (đồng), volume, side (buy/sell/other).
        Mỗi dòng là một lần khớp riêng như TCBS ghi nhận (BU = mua chủ động, SD = bán chủ động, trống = ATO/ATC)."""
        import pandas as pd
        rows, meta = self.ticks(symbol)
        if not rows:
            return None
        day = meta.get("day") or ""
        try:
            d, m = (int(x) for x in day.split("/")[:2])
            today = dt.datetime.now(VN).date()
            year = today.year - (1 if m > today.month else 0)
            date = dt.date(year, m, d)
        except Exception:
            date = dt.datetime.now(VN).date()
        side = {"BU": "buy", "SD": "sell"}
        return pd.DataFrame({
            "time": pd.to_datetime([f"{date.isoformat()} {r.get('t')}" for r in rows]),
            "price": [float(r.get("p") or 0) for r in rows],
            "volume": [float(r.get("v") or 0) for r in rows],
            "side": [side.get(r.get("a") or "", "other") for r in rows],
        }).sort_values("time")

    ticks_df = tick_frame

    def bars_daily(self, symbol: str, count: int = 5, kind: str = "stock") -> dict:
        to = int(time.time())
        return self._req("GET", f"/nyx/v2/stock/bars-long-term?ticker={symbol}&type={kind}&resolution=D&to={to}&countBack={count}")


# ---------------------------------------------------------------- token mã hóa trong repo
def _openssl(args: list[str], data: bytes, api_key: str) -> bytes:
    env = {**os.environ, "TCBS_PASS": api_key}
    r = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-md", "sha256", "-a", "-A",
                        "-pass", "env:TCBS_PASS", *args], input=data, capture_output=True, env=env, timeout=30)
    if r.returncode != 0:
        raise TCBSError("openssl lỗi: " + r.stderr.decode(errors="replace")[:200])
    return r.stdout


def save_token(token: str, api_key: str, path: Path = TOKEN_FILE) -> dict:
    exp = TCBSClient.token_expiry(token)
    blob = json.dumps({"token": token, "exp": exp.get("exp"), "saved": dt.datetime.now(VN).isoformat(timespec="seconds")})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_openssl(["-salt"], blob.encode(), api_key) + b"\n")
    return exp


def load_token(api_key: str | None = None, path: Path = TOKEN_FILE, margin_min: int = 20) -> dict | None:
    """Token còn hạn (ít nhất margin_min phút) hoặc None."""
    api_key = api_key or os.environ.get("TCBS_API_KEY", "")
    if not api_key or not path.exists():
        return None
    try:
        j = json.loads(_openssl(["-d"], path.read_bytes().strip() + b"\n", api_key))
    except Exception:
        return None
    exp = j.get("exp")
    if not exp or dt.datetime.fromisoformat(exp) < dt.datetime.now(VN) + dt.timedelta(minutes=margin_min):
        return None
    _mask(j["token"])
    return j


def status(api_key: str | None = None) -> dict:
    j = load_token(api_key, margin_min=0)
    return {"valid": bool(j), "exp": j.get("exp") if j else None}


def client_from_env() -> TCBSClient | None:
    j = load_token()
    return TCBSClient(j["token"]) if j else None


def login_and_save():
    api_key, otp = os.environ.get("TCBS_API_KEY", ""), os.environ.get("TCBS_OTP", "").strip()
    _mask(api_key)
    _mask(otp)
    if not api_key:
        sys.exit("Thiếu TCBS_API_KEY (GitHub Secrets).")
    c = TCBSClient()
    try:
        tok = c.login(api_key, otp)
    except Exception as e:
        print("Đăng nhập TCBS thất bại:", e)
        sys.exit(1)
    exp = save_token(tok, api_key)
    assert load_token(api_key, margin_min=0), "không đọc lại được token đã lưu"
    print("Đăng nhập TCBS thành công. Token còn hiệu lực đến", exp.get("exp"))


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
    elif len(sys.argv) > 1 and sys.argv[1] == "--login":
        login_and_save()
    elif len(sys.argv) > 1 and sys.argv[1] == "--status":
        print(status())
