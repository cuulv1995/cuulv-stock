#!/usr/bin/env bash
# Cài vnstock + vnai từ kho gói của Vnstock. Nếu một gói phụ thuộc không còn trên kho (ví dụ vnstock_ezchart,
# chỉ dùng để vẽ biểu đồ), cài vnstock không kèm phụ thuộc, tự cài các phụ thuộc còn lại và thay gói thiếu
# bằng một gói rỗng để `import vnstock` vẫn chạy.
set -u
IDX="--extra-index-url https://vnstocks.com/api/simple"
OPTIONAL="vnstock_ezchart vnstock-ezchart"

if pip install -q $IDX vnstock vnai; then
  echo "Đã cài vnstock (đầy đủ phụ thuộc)."
else
  echo "Cài đầy đủ thất bại, chuyển sang cài từng phần."
  TMP=$(mktemp -d)
  pip download -q --no-deps -d "$TMP" $IDX vnstock vnai || { echo "Không tải được vnstock"; exit 0; }
  python - "$TMP" $OPTIONAL <<'PY'
import sys, zipfile, glob, re, subprocess
tmp, skip = sys.argv[1], {s.lower().replace("-", "_") for s in sys.argv[2:]}
reqs = []
for whl in glob.glob(f"{tmp}/*.whl"):
    with zipfile.ZipFile(whl) as z:
        meta = next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))
        for line in z.read(meta).decode().splitlines():
            if line.startswith("Requires-Dist:"):
                spec = line.split(":", 1)[1].strip()
                if "extra ==" in spec:
                    continue
                name = re.split(r"[ ;<>=!~\[(]", spec, 1)[0].lower().replace("-", "_")
                if name in skip or name in ("vnai", "vnstock"):
                    print("Bỏ qua phụ thuộc:", spec)
                    continue
                reqs.append(spec.split(";")[0].strip())
print("Phụ thuộc:", reqs)
if reqs:
    subprocess.call([sys.executable, "-m", "pip", "install", "-q", "--extra-index-url", "https://vnstocks.com/api/simple", *reqs])
PY
  pip install -q --no-deps "$TMP"/*.whl
fi

# Gói rỗng thay cho các gói tùy chọn bị thiếu
python - $OPTIONAL <<'PY'
import importlib, site, sys, pathlib
for name in {n.replace("-", "_") for n in sys.argv[1:]}:
    try:
        importlib.import_module(name)
    except ModuleNotFoundError:
        d = pathlib.Path(site.getsitepackages()[0]) / name
        d.mkdir(exist_ok=True)
        (d / "__init__.py").write_text('"""Gói rỗng thay thế (vẽ biểu đồ, không dùng trong MCDX)."""\n'
                                       "def __getattr__(attr):\n    raise ImportError(f'{__name__} không có sẵn')\n")
        print("Đã tạo gói rỗng:", name)
PY
python -c "import vnstock; from vnstock.explorer.kbs.quote import Quote; print('vnstock', getattr(vnstock, '__version__', '?'), 'OK')" \
  || echo "Không import được vnstock"
