# Bảng dòng tiền MCDX – sàn HOSE

Tool tự chạy mỗi ngày giao dịch lúc **17:05** (giờ Việt Nam) trên GitHub:

1. Lấy dữ liệu từ **SSI FastConnect Data**: rổ VN30 và VNMidcap, giá, khối lượng, khối ngoại.
2. Tính MCDX và các chỉ số: đèn thị trường, Top 5 tín hiệu, Top 10 VN30 và Top 10 VNMidcap dòng tiền mới vào, Top 10 duy trì, cảnh báo thoát, dòng tiền theo ngành, hiệu quả tín hiệu.
3. Đăng dashboard lên **GitHub Pages**. Anh chỉ cần mở link, xem được cả trên điện thoại.

Nếu lúc 17:05 SSI chưa có dữ liệu phiên hôm nay, tool tự thử lại lúc 17:35 và 18:05. Thứ 7 và chủ nhật tool không chạy.

---

## Cài đặt (làm 1 lần, khoảng 15 phút)

### Bước 1. Tạo repo trên GitHub
1. Đăng nhập [github.com](https://github.com). Nếu chưa có tài khoản, đăng ký miễn phí.
2. Bấm **New repository**:
   - Đặt tên, ví dụ `mcdx-hose`.
   - Chọn **Public**. GitHub Pages miễn phí chỉ dùng được với repo Public. Key SSI vẫn an toàn vì nó nằm trong mục Secrets, không ai xem được.
   - Bấm **Create repository**.
3. Đưa code lên repo. Có 2 cách:
   - **Cách dễ nhất:** báo tên repo cho Claude để Claude đẩy code lên giúp.
   - **Tự tải lên:** bấm **uploading an existing file**, kéo toàn bộ file và thư mục trong gói này vào, rồi bấm **Commit changes**.
     Lưu ý: thư mục `.github` là thư mục ẩn. Nếu không thấy nó, bật chế độ hiện file ẩn: trên macOS nhấn Cmd+Shift+. ; trên Windows vào File Explorer, tab View, tích **Hidden items**.

### Bước 2. Nhập key SSI (KHÔNG dán key vào chat hay vào code)
Vào repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**. Tạo 2 secret:

| Name | Secret |
| --- | --- |
| `SSI_CONSUMER_ID` | ConsumerID của anh |
| `SSI_CONSUMER_SECRET` | ConsumerSecret của anh |

Key lấy trên iBoard của SSI, sau khi đã đăng ký dịch vụ FastConnect Data.

### Bước 3. Bật GitHub Pages
Vào **Settings** → **Pages** → mục **Source**, chọn **GitHub Actions**.

### Bước 4. Chạy thử
1. Mở tab **Actions**. Nếu GitHub hỏi, bấm **I understand my workflows, go ahead and enable them**.
2. Chọn **Cập nhật dashboard MCDX** → **Run workflow**:
   - **Lần 1:** tích ô *Chạy bằng dữ liệu giả lập*. Lần này kiểm tra Pages hoạt động, không cần key.
   - **Lần 2:** bỏ tích ô giả lập, tích ô *Chạy lại dù hôm nay đã chạy xong*. Lần này lấy dữ liệu SSI thật. Lần đầu tool tải lịch sử khoảng 400 ngày của khoảng 90 mã nên mất 5–10 phút; những ngày sau chỉ mất 1–2 phút.
3. Khi chạy xong (dấu tích xanh), link dashboard nằm trong bước **deploy**, có dạng:
   `https://cuulv1995.github.io/cuulv-stock/`
   Lưu link này lại, hoặc thêm vào màn hình chính điện thoại.

---

## Sử dụng hằng ngày
- Sau 17:30 các ngày thứ 2 đến thứ 6, mở link dashboard.
- **Cảnh báo thoát:** sửa file `config/holdings.txt` ngay trên GitHub (bấm biểu tượng bút chì). Ghi mỗi dòng 1 mã đang giữ, rồi bấm **Commit changes**. Từ lần chạy sau, dashboard sẽ báo Giữ, Theo dõi hoặc Thoát cho từng mã.
- **Ngành của mã:** sửa trong `config/sectors.json`. Mã không có trong file sẽ được xếp vào ngành "Khác".
- **Ngưỡng và tham số:** nằm ở đầu file `mcdx/analysis.py`, ví dụ GTGD tối thiểu 5 tỷ, ngưỡng tăng nóng 10%, số mã mỗi danh sách.

## Khi có lỗi
GitHub sẽ gửi email khi một lần chạy bị lỗi. Mở tab **Actions** → chọn lần chạy màu đỏ → xem dòng **Lỗi:** ở bước *Lấy dữ liệu SSI*.

| Thông báo | Cách xử lý |
| --- | --- |
| Thiếu SSI_CONSUMER_ID / SSI_CONSUMER_SECRET | Làm lại Bước 2. Tên secret phải viết đúng chữ hoa. |
| Không lấy được token SSI | Key sai hoặc đã hết hạn. Tạo lại key trên iBoard rồi cập nhật secret. |
| Lỗi kết nối hoặc hết thời gian chờ | SSI có thể chặn kết nối từ máy chủ nước ngoài. Báo Claude để chuyển sang chạy trên máy tính của anh (self-hosted runner). |
| Chưa có dữ liệu phiên hôm nay | Bình thường nếu là ngày lễ. Dashboard sẽ giữ phiên gần nhất. |

Các lưu ý khác:
- Lịch chạy của GitHub có thể trễ 5–30 phút so với giờ hẹn.
- Với repo Public, GitHub tự tắt lịch chạy nếu repo không có hoạt động trong 60 ngày. Tool commit dữ liệu mỗi ngày nên thường không bị tắt. Nếu bị tắt, vào tab **Actions** và bấm **Enable workflow**.

## Tiêu chí chọn cổ phiếu
Xem [docs/tieu-chi-co-phieu-tot.md](docs/tieu-chi-co-phieu-tot.md): 10 tiêu chí cơ bản (tăng trưởng, hiệu quả, sức khỏe tài chính, định giá) và 10 tiêu chí thời điểm mà dashboard đã tính.

## Cấu trúc
```
.github/workflows/daily.yml   lịch chạy + đăng GitHub Pages
mcdx/ssi.py                   gọi API SSI FastConnect Data
mcdx/analysis.py              MCDX, danh sách, đèn thị trường, Top 5, cảnh báo
mcdx/run.py                   chương trình chính
template/dashboard.html       giao diện dashboard
config/                       mã đang giữ, ngành, rổ dự phòng
docs/                         tiêu chí chọn cổ phiếu
data/                         dữ liệu tự cập nhật (đừng sửa tay)
tests/test_pipeline.py        chạy thử với SSI giả lập
```

Chạy trên máy tính (không bắt buộc):
```
pip install -r requirements.txt
python -m mcdx.run --demo          # thử bằng dữ liệu giả lập, mở site/index.html
```

---
Công cụ sàng lọc theo quy tắc cố định, không phải khuyến nghị đầu tư.
