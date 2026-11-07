# Thiết kế REST API — Phân hệ Đóng học phí iBanking

Tài liệu đặc tả đầy đủ các REST API của hệ thống (bao gồm **Public API** qua API Gateway ở cổng `8080` và **Internal API** giao tiếp giữa các Microservices có xác thực bằng header `X-Service-Key`).

---

## 1. Bảng tổng hợp các REST API

### 1.1. Public API (Client gọi qua Gateway `http://127.0.0.1:8080/api`)

| STT | Chức năng | HTTP Method | URI | Service xử lý đích | Xác thực |
|---:|---|---|---|---|---|
| 1 | Đăng nhập | `POST` | `/api/auth/login` | User Service (`:8001`) | Không |
| 2 | Đăng xuất | `POST` | `/api/auth/logout` | User Service (`:8001`) | `Bearer Token` |
| 3 | Lấy thông tin người nộp & số dư | `GET` | `/api/auth/me` hoặc `/api/account` | User Service (`:8001`) | `Bearer Token` |
| 4 | Tra cứu học phí theo MSSV | `GET` | `/api/tuition/{student_id}` | Payment (`:8002`) → Tuition (`:8004`) | `Bearer Token` |
| 5 | Khởi tạo giao dịch thanh toán & gửi OTP | `POST` | `/api/payments` | Payment Service (`:8002`) | `Bearer Token` |
| 6 | Lấy chi tiết/kết quả một giao dịch | `GET` | `/api/payments/{transaction_id}` | Payment Service (`:8002`) | `Bearer Token` |
| 7 | Xác nhận OTP & hoàn tất thanh toán | `POST` | `/api/payments/{transaction_id}/confirm` | Payment Service (`:8002`) | `Bearer Token` |
| 8 | Lấy lịch sử giao dịch của người dùng | `GET` | `/api/transactions` | User (`:8001`) → Payment (`:8002`) | `Bearer Token` |

### 1.2. Internal API (Giao tiếp nội bộ giữa các Microservices, bắt buộc `X-Service-Key`)

| STT | Chức năng | HTTP Method | URI | Service cung cấp | Service gọi |
|---:|---|---|---|---|---|
| 1 | Lấy thông tin liên hệ người nộp | `GET` | `/internal/users/{user_id}` | User Service (`:8001`) | Notification Service |
| 2 | Trừ tiền tài khoản có điều kiện (Idempotent) | `POST` | `/internal/accounts/debit` | User Service (`:8001`) | Payment Service |
| 3 | Hoàn tác trừ tiền (Saga Compensation) | `POST` | `/internal/accounts/refund` | User Service (`:8001`) | Payment Service |
| 4 | Tra cứu khoản học phí nội bộ | `GET` | `/internal/tuition/{student_id}` | Tuition Service (`:8004`) | Payment Service |
| 5 | Giữ chỗ tạm thời khoản học phí | `POST` | `/internal/tuition/{student_id}/reserve` | Tuition Service (`:8004`) | Payment Service |
| 6 | Gạch nợ học phí thành `PAID` (Atomic) | `POST` | `/internal/tuition/{student_id}/pay` | Tuition Service (`:8004`) | Payment Service |
| 7 | Giải phóng giữ chỗ học phí | `POST` | `/internal/tuition/{student_id}/release` | Tuition Service (`:8004`) | Payment Service |
| 8 | Tạo mã OTP & xếp hàng gửi email | `POST` | `/internal/otp` | Notification Service (`:8003`) | Payment Service |
| 9 | Xác thực mã OTP (Single-use) | `POST` | `/internal/otp/verify` | Notification Service (`:8003`) | Payment Service |
| 10 | Xếp hàng gửi email biên lai thành công | `POST` | `/internal/receipts` | Notification Service (`:8003`) | Payment Service |
| 11 | Truy vấn danh sách giao dịch theo `user_id` | `GET` | `/internal/users/{user_id}/transactions` | Payment Service (`:8002`) | User Service |

---

## 2. Chi tiết từng API phần Tra cứu học phí & Thanh toán (Thành viên 2)

### 2.1. Tra cứu học phí theo MSSV
- **URI**: `/api/tuition/{student_id}`
- **Method**: `GET`
- **Headers**: `Authorization: Bearer <access_token>`
- **Output (`200 OK`)**:
```json
{
  "student_id": "524H0106",
  "student_name": "Phan Trường Nhất Linh",
  "faculty": "Công nghệ thông tin",
  "major": "Kỹ thuật phần mềm",
  "academic_year": "2024-2028",
  "semester": "HK1 (2026-2027)",
  "amount": 18500000,
  "status": "UNPAID",
  "reserved_by": null,
  "paid_transaction_id": null,
  "paid_by_user_id": null,
  "paid_at": null,
  "payer": {
    "id": 1,
    "full_name": "Phan Trường Nhất Linh",
    "phone": "0900000001",
    "email": "linh@example.com",
    "balance": 30000000
  },
  "payer_balance": 30000000,
  "sufficient_balance": true,
  "can_pay": true
}
```
- **HTTP Status Codes**:
  - `200 OK`: Tra cứu thành công.
  - `401 Unauthorized`: Chưa đăng nhập hoặc hết phiên.
  - `404 Not Found` (`TUITION_NOT_FOUND`): MSSV không tồn tại trong hệ thống.

---

### 2.2. Khởi tạo giao dịch thanh toán & yêu cầu gửi OTP
- **URI**: `/api/payments`
- **Method**: `POST`
- **Headers**:
  - `Authorization: Bearer <access_token>`
  - `Content-Type: application/json`
  - `Idempotency-Key: <optional_unique_key>` (tùy chọn chống tạo trùng giao dịch)
- **Request Body**:
```json
{
  "student_id": "524H0106",
  "amount": 18500000,
  "terms_accepted": true
}
```
- **Output (`201 Created` / `200 OK` nếu Idempotent)**:
```json
{
  "id": "pay-3a9f1c2e4b5d6a7f8e9c0d1b",
  "transaction_id": "pay-3a9f1c2e4b5d6a7f8e9c0d1b",
  "user_id": 1,
  "payer_name": "Phan Trường Nhất Linh",
  "payer_phone": "0900000001",
  "payer_email": "linh@example.com",
  "student_id": "524H0106",
  "student_name": "Phan Trường Nhất Linh",
  "semester": "HK1 (2026-2027)",
  "amount": 18500000,
  "status": "PENDING",
  "failure_code": null,
  "failure_message": null,
  "debit_completed": false,
  "tuition_completed": false,
  "receipt_queued": false,
  "created_at": 1770000000,
  "expires_at": 1770000300,
  "updated_at": 1770000000,
  "completed_at": null
}
```
- **HTTP Status Codes**:
  - `201 Created`: Tạo giao dịch thành công và đã xếp hàng gửi email OTP.
  - `400 Bad Request`: Chưa đồng ý điều khoản (`TERMS_REQUIRED`), hoặc truyền số tiền không khớp toàn bộ khoản học phí (`PARTIAL_PAYMENT_NOT_ALLOWED`).
  - `404 Not Found` (`TUITION_NOT_FOUND`): Không tìm thấy MSSV.
  - `409 Conflict`:
    - `TUITION_ALREADY_PAID`: Khoản học phí đã được đóng trước đó.
    - `INSUFFICIENT_BALANCE`: Số dư khả dụng nhỏ hơn số tiền cần thanh toán.

---

### 2.3. Xác nhận giao dịch bằng OTP & Thực thi thanh toán
- **URI**: `/api/payments/{transaction_id}/confirm`
- **Method**: `POST`
- **Headers**:
  - `Authorization: Bearer <access_token>`
  - `Content-Type: application/json`
- **Request Body**:
```json
{
  "otp": "123456"
}
```
- **Output (`200 OK`)**:
```json
{
  "id": "pay-3a9f1c2e4b5d6a7f8e9c0d1b",
  "transaction_id": "pay-3a9f1c2e4b5d6a7f8e9c0d1b",
  "user_id": 1,
  "payer_name": "Phan Trường Nhất Linh",
  "payer_phone": "0900000001",
  "payer_email": "linh@example.com",
  "student_id": "524H0106",
  "student_name": "Phan Trường Nhất Linh",
  "semester": "HK1 (2026-2027)",
  "amount": 18500000,
  "status": "SUCCESS",
  "failure_code": null,
  "failure_message": null,
  "debit_completed": true,
  "tuition_completed": true,
  "receipt_queued": true,
  "created_at": 1770000000,
  "expires_at": 1770000300,
  "updated_at": 1770000045,
  "completed_at": 1770000045
}
```
- **HTTP Status Codes**:
  - `200 OK`: Xác thực OTP, trừ tiền, gạch nợ học phí, lưu lịch sử và xếp hàng email xác nhận thành công.
  - `400 Bad Request` (`OTP_INVALID`): Nhập sai mã OTP (vẫn còn lượt thử, tối đa 5 lần).
  - `404 Not Found` (`PAYMENT_NOT_FOUND`): Giao dịch không tồn tại hoặc không thuộc tài khoản đang đăng nhập.
  - `409 Conflict`:
    - `INSUFFICIENT_BALANCE`: Tại thời điểm trừ tiền, số dư không còn đủ (do giao dịch đồng thời khác trên cùng tài khoản đã trừ trước).
    - `TUITION_ALREADY_PAID`: Khoản học phí vừa được tài khoản khác thanh toán thành công trước; hệ thống tự động hoàn tiền (`refund`) về tài khoản người nộp.
    - `OTP_ALREADY_USED`: Mã OTP đã được sử dụng.
  - `410 Gone` (`OTP_EXPIRED`): Mã OTP đã quá hạn 5 phút.
  - `429 Too Many Requests` (`OTP_LOCKED`): Nhập sai OTP đủ 5 lần.

---

### 2.4. Lấy thông tin giao dịch & Lịch sử giao dịch
- **Chi tiết 1 giao dịch**: `GET /api/payments/{transaction_id}` → `200 OK` (trả về đối tượng giao dịch như trên).
- **Lịch sử giao dịch của người nộp**: `GET /api/transactions` → `200 OK`:
```json
{
  "items": [
    {
      "id": "pay-3a9f1c2e4b5d6a7f8e9c0d1b",
      "student_id": "524H0106",
      "student_name": "Phan Trường Nhất Linh",
      "semester": "HK1 (2026-2027)",
      "amount": 18500000,
      "status": "SUCCESS",
      "created_at": 1770000000,
      "completed_at": 1770000045
    }
  ]
}
```
