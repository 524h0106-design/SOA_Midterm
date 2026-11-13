# SOA Midterm iBanking

Hệ thống mô phỏng đóng học phí với User, Notification, Payment, Tuition và Gateway. OTP và biên nhận gửi qua Gmail SMTP vào hộp thư thật. Các số dư, điện thoại và khoản học phí vẫn là dữ liệu mô phỏng của dự án.

## Cấu hình một lần

Cần Python 3.12. Mở thư mục chứa `run.py` trong VS Code.

1. Bật xác minh 2 bước cho Gmail dùng để gửi tại https://myaccount.google.com/security.
2. Tạo mật khẩu ứng dụng Google tại https://myaccount.google.com/apppasswords. Nếu tài khoản không hỗ trợ mục này, dùng một tài khoản gửi khác hỗ trợ mật khẩu ứng dụng.
3. Chạy trong Terminal:

```powershell
py configure_email.py
```

Nhấn Enter để dùng `linhkatie0707@gmail.com` gửi thư, hoặc nhập Gmail gửi khác. Dán mật khẩu ứng dụng 16 ký tự rồi Enter. Terminal không hiện mật khẩu khi nhập. Không dùng mật khẩu đăng nhập Gmail.

Script lưu cấu hình vào `.env`, giữ hoặc tự tạo SERVICE_KEY hợp lệ. Không đưa `.env` lên GitHub. Mật khẩu SMTP được Gmail kiểm tra khi thực sự gửi thư; script cấu hình chưa gửi thư thử.

## Chạy bằng Python

Dừng các service cũ bằng Ctrl+C. Chạy:

```powershell
py run.py
```

Mở http://localhost:8080. Không cần Mailpit hoặc Docker cho cách chạy này.

| Username | Mật khẩu mẫu | Người nộp | Email nhận OTP và biên nhận |
|---|---|---|---|
| linh | Demo@123 | Phan Trường Nhất Linh | linhkatie0707@gmail.com |
| minh | Demo@123 | Nguyễn Tiến | 524H0128@student.tdtu.edu.vn |

Giữ username `minh` cho tài khoản thứ hai như code gốc của nhóm. User Service cập nhật hai email cả khi database đã tồn tại, không đặt lại số dư, mật khẩu hoặc lịch sử. Nếu đã đổi mật khẩu, tiếp tục dùng mật khẩu đã đổi.

Chọn MSSV chưa đóng, đồng ý điều khoản và xác nhận để nhận OTP. Thư gửi cho người nộp đang đăng nhập, kể cả đóng hộ sinh viên khác. Mở hộp thư thật, kiểm tra cả Spam. Sau thanh toán thành công có thêm email biên nhận.

Để thử OTP riêng, giữ web đang chạy, mở Terminal thứ hai và chạy `py demo_otp.py`. Bước này gửi email đến Linh nhưng không trừ tiền.

## Chạy bằng Docker

Sau khi cấu hình `.env` bằng script trên, chạy `docker compose up --build -d`. Mở http://localhost:8080. Không chạy đồng thời bộ Python trên cùng cổng.

Khi thay code vào bản cũ, giữ `.env`, `data` và các Docker volume của mình. Chạy lại script cấu hình rồi khởi động lại các service. Không dùng `docker compose down -v` vì sẽ xóa dữ liệu.

## Kiểm thử và lỗi thường gặp

Chạy `py -m unittest discover -s tests -v`. Đã qua 34 kiểm thử, gồm luồng thanh toán, concurrency, migration email và SMTP giả lập; test không gửi thư ra ngoài. Chưa thử giao thư thật vì cần mật khẩu ứng dụng trên máy người dùng; Docker chưa chạy kiểm chứng trong môi trường chỉnh sửa.

- Báo thiếu cấu hình Gmail: chạy lại `py configure_email.py`.
- SMTPAuthenticationError: kiểm tra Gmail gửi và mật khẩu ứng dụng.
- Không kết nối SMTP: kiểm tra mạng, tường lửa và cổng 587.
- Không thấy thư: kiểm tra đúng email người nộp và mục Spam.

Tham khảo Google: https://support.google.com/accounts/answer/185833 và https://developers.google.com/workspace/gmail/imap/imap-smtp.
