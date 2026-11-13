import getpass
import json
import os
import secrets
from pathlib import Path
from services.mail_config import LINH_EMAIL, TIEN_EMAIL, load_mail_config

ROOT = Path(__file__).resolve().parent


def read_values(path):
    result = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def configure(sender, password, root=ROOT):
    root = Path(root)
    path = root / '.env'
    old = read_values(path)
    values = {
        'SMTP_HOST': 'smtp.gmail.com', 'DOCKER_SMTP_HOST': 'smtp.gmail.com',
        'SMTP_PORT': '587', 'SMTP_SECURITY': 'starttls',
        'SMTP_USER': sender.strip(), 'SMTP_PASSWORD': ''.join(password.split()),
        'MAIL_FROM': sender.strip(), 'DEMO_LINH_EMAIL': LINH_EMAIL, 'DEMO_SECOND_EMAIL': TIEN_EMAIL,
    }
    load_mail_config(values)
    key = old.get('SERVICE_KEY', '')
    if len(key) < 32 or key.startswith('REPLACE_'):
        keyfile = root / 'data' / 'runtime-key.json'
        try:
            key = json.loads(keyfile.read_text(encoding='utf-8'))['SERVICE_KEY']
        except (OSError, ValueError, KeyError):
            key = ''
        if not isinstance(key, str) or len(key) < 32 or key.startswith('REPLACE_'):
            key = secrets.token_urlsafe(48)
    values['SERVICE_KEY'] = key
    lines = path.read_text(encoding='utf-8-sig').splitlines() if path.exists() else []
    kept = [line for line in lines if line.strip().split('=', 1)[0].strip() not in values]
    content = '\n'.join(kept + [f'{name}={value}' for name, value in values.items()]) + '\n'
    temporary = root / '.env.tmp'
    with temporary.open('w', encoding='utf-8', newline='\n') as stream:
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        stream.write(content)
    os.replace(temporary, path)
    return values


def main():
    print('Cấu hình gửi OTP và biên nhận vào hộp thư thật bằng Gmail SMTP.')
    print('Bật xác minh 2 bước và tạo mật khẩu ứng dụng ở https://myaccount.google.com/apppasswords')
    sender = input(f'Gmail dùng để gửi [Enter = {LINH_EMAIL}]: ').strip() or LINH_EMAIL
    password = getpass.getpass('Dán mật khẩu ứng dụng Google 16 ký tự (không hiện khi nhập): ')
    configure(sender, password)
    print('Đã lưu cấu hình .env trên máy này. Chưa gửi thư thử.')
    print(f'Người nhận: linh -> {LINH_EMAIL}; tài khoản thứ hai minh -> {TIEN_EMAIL}.')
    print('Dừng các service cũ. Chạy py run.py hoặc docker compose up --build -d để áp dụng.')
    print('Không đưa .env lên GitHub.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        print('Không lưu được cấu hình:', str(error))
        raise SystemExit(1)
