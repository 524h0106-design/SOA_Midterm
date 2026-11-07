# Hướng dẫn Tích hợp & Kịch bản Trình diễn (Demo)

Tài liệu hướng dẫn cách kết nối giữa phần **Thành viên 1 (User, Notification, Gateway)** và **Thành viên 2 (Tuition, Payment, Giao diện Thanh toán & Concurrency)** cùng kịch bản demo bảo vệ đồ án.

---

## 1. Cấu hình Cổng và Biến môi trường

| Service | File thực thi | Cổng mặc định | Database SQLite |
|---|---|---:|---|
| API Gateway & Web UI | `services/gateway.py` | `8080` | — |
| User Service | `services/user_service.py` | `8001` | `data/users.db` |
| Payment Service | `services/payment_service.py` | `8002` | `data/payments.db` |
| Notification Service | `services/notification_service.py` | `8003` | `data/notifications.db` |
| Tuition Service | `services/tuition_service.py` | `8004` | `data/tuitions.db` |
| Mailpit (SMTP / Web) | Docker `axllent/mailpit` | `1025` / `8025` | — |

Tất cả các service sử dụng chung biến môi trường `SERVICE_KEY` (tối thiểu 32 ký tự) trong file `.env` hoặc `data/runtime-key.json` để xác thực header `X-Service-Key` khi gọi các API `/internal/*`.

---

## 2. Dữ liệu Mẫu (Seed Data) phục vụ Demo

### 2.1. Tài khoản người nộp tiền (`User Service`)
| Username | Password | Họ và tên | Số dư ban đầu |
|---|---|---|---:|
| `linh` | `Demo@123` | Phan Trường Nhất Linh | `30.000.000 VND` |
| `minh` | `Demo@123` | Tài khoản kiểm thử thứ hai | `30.000.000 VND` |

### 2.2. Sinh viên và Khoản học phí (`Tuition Service`)
| MSSV | Họ và tên sinh viên | Khoa / Ngành | Học kỳ | Số tiền học phí | Trạng thái ban đầu | Mục đích Demo |
|---|---|---|---|---:|---|---|
| `524H0106` | Phan Trường Nhất Linh | CNTT / Kỹ thuật phần mềm | HK1 (2026-2027) | `18.500.000 VND` | `UNPAID` | Luồng thanh toán thành công chuẩn |
| `524H0107` | Nguyễn Văn Minh | CNTT / Khoa học máy tính | HK1 (2026-2027) | `15.000.000 VND` | `UNPAID` | Kết hợp với `524H0106` để test đồng thời trên 1 tài khoản (`18.5tr + 15tr > 30tr`) |
| `524H0108` | Trần Thị Mai Anh | QTKD / Tài chính ngân hàng | HK1 (2026-2027) | `12.000.000 VND` | `UNPAID` | Test 2 tài khoản (`linh` & `minh`) cùng đóng 1 MSSV |
| `524H0109` | Lê Hoàng Nam | Điện - Điện tử | HK1 (2026-2027) | `35.000.000 VND` | `UNPAID` | Test **Thiếu số dư** (`35tr > 30tr`), nút xác nhận tự khóa |
| `524H0110` | Phạm Minh Tuấn | Ngoại ngữ | HK1 (2026-2027) | `14.000.000 VND` | `PAID` | Test **Học phí đã đóng**, không cho phép đóng lại |

---

## 3. Kịch bản Trình diễn (Demo) khi Báo cáo

1. **Khởi chạy hệ thống**:
   - Bật Mailpit: `docker compose up -d mailpit` (mở http://127.0.0.1:8025).
   - Bật toàn bộ 5 microservices: `py run.py` (mở http://127.0.0.1:8080).
2. **Demo 1 — Kiểm tra điều kiện tra cứu học phí**:
   - Đăng nhập bằng `linh` / `Demo@123`.
   - Nhập MSSV không tồn tại (`524H9999`) → Báo lỗi `404 Không tìm thấy sinh viên`.
   - Chọn MSSV `524H0110` (Đã đóng) → Hiển thị trạng thái `Đã thanh toán`, nút **Xác nhận giao dịch** bị vô hiệu hóa.
   - Chọn MSSV `524H0109` (`35.000.000 VND`) → Báo thiếu số dư (`30.000.000 VND < 35.000.000 VND`), nút **Xác nhận giao dịch** bị vô hiệu hóa.
3. **Demo 2 — Thanh toán học phí thành công toàn luồng**:
   - Chọn MSSV `524H0106` (`18.500.000 VND`).
   - Tích chọn *"Tôi đã kiểm tra thông tin và đồng ý với các thỏa thuận, điều khoản"* → Nút **Xác nhận giao dịch** sáng lên.
   - Nhấn **Xác nhận giao dịch** → Tự động chuyển sang tab **Xác thực OTP** kèm đồng hồ đếm ngược 5 phút.
   - Mở Mailpit (`http://127.0.0.1:8025`), lấy mã OTP 6 chữ số vừa gửi tới `linh@example.com`, nhập vào ô OTP và bấm **Xác nhận thanh toán**.
   - Kiểm tra kết quả: Số dư giảm còn `11.500.000 VND`, học phí `524H0106` chuyển thành `Đã thanh toán`, tab **Lịch sử giao dịch** ghi nhận giao dịch `Thành công`, và Mailpit nhận được email biên lai *"Thanh toán học phí thành công"*.
4. **Demo 3 — Kiểm thử tự động Concurrency & Saga Compensation**:
   - Chạy lệnh:
     ```powershell
     py -m unittest discover -s tests -v
     ```
   - Trình bày 2 bài test đồng thời:
     - `test_concurrent_transactions_same_account_never_overdraw`: Cùng 1 tài khoản xác nhận đồng thời 2 khoản học phí vượt tổng số dư → Chỉ 1 giao dịch thành công, 1 giao dịch bị từ chối `INSUFFICIENT_BALANCE`.
     - `test_concurrent_accounts_same_tuition_paid_once_and_refunded`: Hai tài khoản `linh` và `minh` xác nhận đồng thời cho cùng MSSV `524H0108` → Khoản học phí chỉ được gạch nợ đúng 1 lần, tài khoản thua cuộc được hoàn tiền tự động (`refund`) về nguyên `30.000.000 VND`.
