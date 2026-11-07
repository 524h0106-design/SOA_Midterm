# Hướng dẫn Quản lý Mã nguồn trên GitHub

1. **Không đưa dữ liệu runtime và khóa bí mật lên GitHub**:
   - File `.gitignore` đã loại trừ:
     - `.env` (chứa `SERVICE_KEY` và mật khẩu SMTP)
     - `data/` (chứa các file SQLite `users.db`, `tuitions.db`, `payments.db`, `notifications.db` và `runtime-key.json`)
     - `__pycache__/`, `*.pyc`
2. **Các bước đẩy dự án lên GitHub**:
   ```powershell
   git init
   git add .
   git commit -m "Complete SOA Midterm iBanking Tuition Payment System"
   git branch -M main
   git remote add origin <URL_REPO_GITHUB>
   git push -u origin main
   ```
3. **Khi thành viên khác hoặc giảng viên clone về**:
   - Chỉ cần chạy `py run.py` (tự động tạo `data/` và khóa nội bộ) hoặc sao chép `.env.example` thành `.env` rồi chạy `docker compose up --build -d`.
