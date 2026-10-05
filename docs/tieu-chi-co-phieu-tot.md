# Tiêu chí một cổ phiếu tốt đáng đầu tư

Một mã đáng mua khi đạt đủ hai vế:

- **Doanh nghiệp tốt** (vế cơ bản).
- **Thời điểm tốt** (vế kỹ thuật và dòng tiền).

Tool hiện tự lọc vế thời điểm từ dữ liệu giá của SSI. Vế cơ bản phải kiểm tra bằng báo cáo tài chính, vì SSI FastConnect Data không cung cấp loại dữ liệu này.

## Vế 1: doanh nghiệp tốt (cơ bản)

| Nhóm | Chỉ số | Ngưỡng tham khảo | Vì sao quan trọng |
| --- | --- | --- | --- |
| Tăng trưởng | Lợi nhuận trên mỗi cổ phiếu (EPS) quý gần nhất so với cùng kỳ năm trước | ≥ 20–25% | Giá cổ phiếu đi theo lợi nhuận; lợi nhuận tăng mạnh là lý do để tiền lớn vào |
| Tăng trưởng | EPS 3 năm gần nhất | Tăng đều, ≥ 15%/năm | Loại doanh nghiệp chỉ tăng 1 quý nhờ khoản bất thường |
| Tăng trưởng | Doanh thu quý so với cùng kỳ | ≥ 15% | Lợi nhuận tăng nhờ bán được hàng, không phải nhờ cắt giảm chi phí |
| Hiệu quả | ROE | ≥ 15% | Vốn của cổ đông sinh lời tốt |
| Hiệu quả | Biên lợi nhuận gộp và biên lợi nhuận ròng | Ổn định hoặc tăng dần | Có lợi thế cạnh tranh, giữ được giá bán |
| Sức khỏe | Nợ vay / vốn chủ sở hữu | < 1 lần (không áp dụng cho ngân hàng, chứng khoán) | Ít rủi ro khi lãi suất tăng |
| Sức khỏe | Dòng tiền từ hoạt động kinh doanh | Dương, xấp xỉ lợi nhuận | Lợi nhuận là tiền thật, không chỉ trên sổ sách |
| Định giá | P/E so với trung bình ngành; PEG = P/E ÷ % tăng EPS | P/E không cao hơn ngành quá nhiều; PEG < 1 | Tránh mua đắt so với tốc độ tăng trưởng |
| Ngân hàng | Nợ xấu (NPL), tỷ lệ tiền gửi không kỳ hạn (CASA), biên lãi ròng (NIM), P/B | NPL < 2%; CASA và NIM tăng; P/B hợp lý so với ROE | Ngân hàng được định giá theo tài sản và chất lượng cho vay |
| Chất lượng | Vị thế ngành, ban lãnh đạo, giao dịch nội bộ, cổ tức | Dẫn đầu ngành; người nội bộ không bán ròng; cổ tức đều đặn | Giảm rủi ro quản trị, minh bạch |

## Vế 2: thời điểm tốt (kỹ thuật và dòng tiền, tool đã tính)

| Chỉ số | Ngưỡng tham khảo | Xem ở đâu trên dashboard |
| --- | --- | --- |
| Xu hướng thị trường | Đèn ở trạng thái Tấn công hoặc Thận trọng | Đèn xu hướng |
| Xu hướng cổ phiếu | Giá trên MA50 và MA200, MA200 đang đi lên | Biểu đồ chi tiết |
| Sức mạnh giá so với thị trường (RS) | ≥ 80 | Cột RS |
| Khoảng cách tới đỉnh 52 tuần | Cách đỉnh ≤ 15% | Thành phần "Nền giá" trong Top 5 |
| Nền giá | Biên độ 20 phiên ≤ 15%, khối lượng giảm dần trong nền | Thành phần "Nền giá" trong Top 5 |
| Dòng tiền lớn | Banker > 0 và nằm trên đường MA10 | 3 danh sách MCDX |
| Khối lượng xác nhận | Khối lượng / trung bình 20 phiên (KL/TB20) ≥ 1,5 khi giá tăng | Cột KL/TB20 |
| Khối ngoại | Mua ròng trong 5 phiên | Cột Ngoại 5p |
| Chưa tăng nóng | Giá cách MA20 ≤ 10% | Nhãn "Tăng nóng" |
| Thanh khoản | Giá trị giao dịch trung bình 20 phiên (GTGD TB20) ≥ 5–10 tỷ | Bộ lọc đầu vào |

## Cách dùng hai vế

1. Tool lọc ra các mã đúng thời điểm: Top 5 và 3 danh sách.
2. Với từng mã, kiểm tra vế cơ bản theo bảng ở trên. Ưu tiên mã đạt từ 6/10 tiêu chí trở lên. Hai tiêu chí bắt buộc là EPS tăng trưởng và dòng tiền kinh doanh dương.
3. Chỉ giải ngân trong mức mà đèn thị trường cho phép, và đặt lệnh cắt lỗ theo mức gợi ý trên dashboard.

## Hướng mở rộng

Có thể tự động hóa vế cơ bản: lấy báo cáo tài chính từ vnstock (nhóm `Fundamental`) và cập nhật mỗi quý. Sau đó thêm điểm cơ bản thang 0–10 vào Top 5 và vào trang chi tiết từng mã.

---
Các ngưỡng trên là quy tắc phổ biến, chỉ để tham khảo, không phải khuyến nghị đầu tư.
