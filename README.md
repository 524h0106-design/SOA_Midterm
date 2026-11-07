# Phần thực hiện của Linh

Dự án SOA Midterm — phân hệ đóng học phí iBanking: User Service, OTP và email trong Notification Service, giao diện đăng nhập, tài khoản, lịch sử và xác thực OTP.

## Chạy nhanh trên Windows

Cài Python 3.12, mở thư mục `nhat-linh` bằng VS Code. Vào Terminal → New Terminal và chạy:

```powershell
py run.py
```

Nếu máy không có lệnh `py`, dùng `python run.py`. Không cần cài thêm thư viện Python. Mở http://127.0.0.1:8080.

| Username | Password | Số dư demo ban đầu |
|---|---|---:|
| linh | Demo@123 | 30.000.000 VND |
| minh | Demo@123 | 30.000.000 VND |

`run.py` tự tạo khóa nội bộ trong `data/runtime-key.json` nếu chưa cấu hình `SERVICE_KEY`. Không đưa thư mục dữ liệu này lên GitHub. Dùng Ctrl+C để dừng.

Đăng nhập và thông tin tài khoản chạy độc lập. Lịch sử, tải giao dịch và xác nhận thanh toán gọi Payment Service của thành viên 2 ở cổng 8002. Khi dịch vụ này chưa có, giao diện hiển thị chưa kết nối. Bản này không tạo lịch sử hay kết quả thanh toán giả.

## Xem email OTP bằng Mailpit

Nếu có Docker Desktop, chạy thêm ở một Terminal khác:

```powershell
docker compose up -d mailpit
```

Mở http://127.0.0.1:8025 để xem email. Mailpit là hộp thư local, không chuyển email đến Gmail thật. OTP chỉ được tạo khi Payment Service gọi API nội bộ tạo challenge; không có mã OTP cố định và không có API công khai trả mã OTP.

Nếu chỉ chạy các test, không cần Docker hoặc Mailpit: bộ test có máy chủ SMTP cục bộ riêng và không gửi email ra ngoài.

Để tự kiểm tra OTP trước khi có Payment Service, giữ `py run.py` và Mailpit đang chạy, mở Terminal mới trong cùng thư mục rồi chạy `py demo_otp.py`. Script xếp hàng email cho một challenge thử nghiệm, chờ bạn nhập mã từ Mailpit và xác minh một lần. Script không trừ tiền và không tạo giao dịch thanh toán. Phần giao diện OTP vẫn cần Payment Service để tải thông tin giao dịch thật.

## Chạy tất cả phần của Linh bằng Docker

1. Sao chép `.env.example` thành `.env`.
2. Sinh khóa bằng `py -c "import secrets; print(secrets.token_urlsafe(48))"`, thay giá trị SERVICE_KEY trong `.env`.
3. Chạy `docker compose up --build -d`.
4. Mở web ở cổng 8080 và Mailpit ở cổng 8025.

Compose dùng ba service ứng dụng và Mailpit, mỗi service nghiệp vụ có volume SQLite riêng. `docker compose down` dừng và giữ dữ liệu. Đổi email demo chỉ có hiệu lực khi seed lần đầu; xem phần cấu hình bên dưới khi đã có dữ liệu.

Nếu Payment Service chạy trên máy host, cấu hình `DOCKER_PAYMENT_URL=http://host.docker.internal:8002`. Payment phải lắng nghe trên địa chỉ Docker có thể kết nối. Nếu cùng một Compose của cả nhóm, đổi thành URL tên service và cấu hình `USER_URL=http://user:8001`, Notification URL tương ứng. Tên, cổng và khóa phải được cả hai thống nhất.

## Gửi đến email thật

Sao chép `.env.example` thành `.env` và điền cấu hình SMTP được nhà cung cấp cấp cho bạn. Hỗ trợ `SMTP_SECURITY=none`, `starttls` hoặc `ssl`; ví dụ STARTTLS thường dùng cổng 587, SSL thường dùng 465. Không commit `.env` hoặc mật khẩu SMTP.

Đặt `DEMO_LINH_EMAIL` trước lần chạy đầu để seed email người nộp. Với dữ liệu đã tồn tại, dừng User Service rồi cập nhật `users.email` trong `data/users.db` bằng công cụ SQLite, hoặc tạo một `DATA_DIR` mới cho demo riêng. Không xóa dữ liệu có giao dịch cần giữ. Chạy lại `py run.py` sau khi đổi cấu hình. Docker dùng `DOCKER_SMTP_HOST` để chọn máy chủ SMTP thay cho Mailpit và cần tạo lại container để nhận biến mới.

## Phần đã thực hiện

| Hạng mục | Thành viên | File chính |
|---|---|---|
| Đăng nhập, đăng xuất, phiên một giờ, giới hạn đăng nhập sai | Thành viên 1 | `services/user_service.py` |
| Hồ sơ người nộp, số dư, trừ tiền có điều kiện, chống trừ lặp, hoàn tác | Thành viên 1 | `services/user_service.py`, `services/user_schema.sql` |
| OTP ngẫu nhiên, gắn giao dịch và người nộp, không trùng mã đang hiệu lực, tối đa 5 phút, dùng thành công một lần | Thành viên 1 | `services/notification_service.py`, `services/notification_schema.sql` |
| Email OTP, email thành công, hàng đợi lưu bền và tự thử lại khi lỗi SMTP | Thành viên 1 | `services/notification_service.py` |
| Sinh viên TDTU, khoản học phí, tra cứu theo MSSV, giữ chỗ & gạch nợ nguyên tử | Thành viên 2 | `services/tuition_service.py`, `services/tuition_schema.sql` |
| Điều phối thanh toán (Saga Orchestrator), kiểm tra điều kiện, trừ tiền, bù trừ hoàn tiền (`refund`) khi xung đột, lưu lịch sử | Thành viên 2 | `services/payment_service.py`, `services/payment_schema.sql` |
| Gateway cho giao diện, chặn API nội bộ khỏi đường dẫn công khai | Chung | `services/gateway.py` |
| Giao diện đăng nhập, tổng quan, tra cứu học phí theo MSSV, điều khoản, xác thực OTP đếm ngược và lịch sử giao dịch | Thành viên 1 & 2 | `web/index.html`, `web/app.js`, `web/style.css` |
| Phân tích yêu cầu, Use Case, ERD tổng thể, Kiến trúc Microservices, hợp đồng API và kịch bản tích hợp | Thành viên 1 & 2 | `docs/PHAN_TICH.md`, `docs/API.md`, `docs/INTEGRATION.md`, `docs/use-case.puml`, `docs/erd.puml`, `docs/architecture.puml` |

## Dữ liệu MSSV mẫu để kiểm thử & trình diễn

| MSSV | Họ và tên sinh viên | Học kỳ | Số tiền học phí | Trạng thái ban đầu | Kịch bản kiểm thử |
|---|---|---|---:|---|---|
| `524H0106` | Phan Trường Nhất Linh | HK1 (2026-2027) | 18.500.000 VND | `UNPAID` | Thanh toán thành công chuẩn |
| `524H0107` | Nguyễn Văn Minh | HK1 (2026-2027) | 15.000.000 VND | `UNPAID` | Kết hợp với `524H0106` để kiểm thử đồng thời trên 1 tài khoản |
| `524H0108` | Trần Thị Mai Anh | HK1 (2026-2027) | 12.000.000 VND | `UNPAID` | Hai tài khoản (`linh`, `minh`) cùng đóng 1 khoản học phí |
| `524H0109` | Lê Hoàng Nam | HK1 (2026-2027) | 35.000.000 VND | `UNPAID` | Kiểm thử không đủ số dư (`35tr > 30tr`) |
| `524H0110` | Phạm Minh Tuấn | HK1 (2026-2027) | 14.000.000 VND | `PAID` | Kiểm thử khoản học phí đã được thanh toán |

## Cấu trúc và kiểm thử

```text
nhat-linh/
  services/
    common.py
    user_service.py
    user_schema.sql
    tuition_service.py
    tuition_schema.sql
    payment_service.py
    payment_schema.sql
    notification_service.py
    notification_schema.sql
    gateway.py
  web/
    index.html
    app.js
    style.css
  tests/test_services.py
  docs/
    API.md
    PHAN_TICH.md
    INTEGRATION.md
    HUONG_DAN_GITHUB.md
    TEST_RESULTS.txt
    use-case.puml
    erd.puml
    architecture.puml
  run.py
  compose.yaml
  Dockerfile
  .env.example
  .gitignore
```

Chạy toàn bộ 24 bài kiểm thử tự động (bao gồm kiểm thử đơn vị, tích hợp toàn luồng và giao dịch đồng thời):

```powershell
py -m unittest discover -s tests -v
```

Xem [hợp đồng API](docs/API.md), [hướng dẫn tích hợp & demo](docs/INTEGRATION.md), [phân tích nghiệp vụ, ERD & kiến trúc](docs/PHAN_TICH.md) và [cách đưa lên GitHub](docs/HUONG_DAN_GITHUB.md).

## Link đề bài

[Đề bài dự án giữa kỳ](https://drive.google.com/file/d/1TE_I5DuHjBuzbgo_Z-5EiAJVIRXY55v8/view)
