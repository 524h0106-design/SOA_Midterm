import json
import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read_env():
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    data = Path(os.environ.get('DATA_DIR', str(ROOT / 'data')))
    data.mkdir(parents=True, exist_ok=True)
    if not os.environ.get('SERVICE_KEY'):
        keyfile = data / 'runtime-key.json'
        if not keyfile.exists():
            keyfile.write_text(json.dumps({'SERVICE_KEY': secrets.token_urlsafe(48)}), encoding='utf-8')
            try:
                keyfile.chmod(0o600)
            except OSError:
                pass
        os.environ['SERVICE_KEY'] = json.loads(keyfile.read_text(encoding='utf-8'))['SERVICE_KEY']
    if len(os.environ['SERVICE_KEY']) < 32 or os.environ['SERVICE_KEY'].startswith('REPLACE_'):
        raise RuntimeError('Hãy thay SERVICE_KEY trong .env bằng chuỗi ngẫu nhiên ít nhất 32 ký tự hoặc bỏ dòng đó để tự tạo.')


def main():
    read_env()
    children = []
    try:
        for service in ['user_service.py', 'tuition_service.py', 'notification_service.py', 'payment_service.py', 'gateway.py']:
            children.append(subprocess.Popen([sys.executable, str(ROOT / 'services' / service)], cwd=ROOT))
        print('Web: http://127.0.0.1:8080 | User: 8001 | Payment: 8002 | Notification: 8003 | Tuition: 8004', flush=True)
        print('Demo: linh / Demo@123 hoặc minh / Demo@123. Email OTP cần SMTP (Mailpit cổng 8025), xem README.md.', flush=True)
        print('MSSV demo: 524H0106 (18.5tr), 524H0107 (15tr), 524H0108 (12tr), 524H0109 (35tr - vượt số dư), 524H0110 (Đã đóng).', flush=True)
        while True:
            for child in children:
                if child.poll() is not None:
                    raise RuntimeError('Một dịch vụ đã dừng. Kiểm tra lỗi phía trên và cổng đang sử dụng.')
            time.sleep(.5)
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == '__main__':
    main()
