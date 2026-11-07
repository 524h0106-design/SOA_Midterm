import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from run import read_env


def main():
    read_env()
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'services'))
    from common import SERVICE_KEY
    transaction_id = 'demo-' + uuid.uuid4().hex

    def post(path, data):
        request = urllib.request.Request('http://127.0.0.1:8003' + path,
            data=json.dumps(data).encode(), headers={'Content-Type': 'application/json', 'X-Service-Key': SERVICE_KEY})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            detail = json.load(error)
            raise RuntimeError(detail['error']['message'])

    print('Kiểm tra OTP độc lập, không trừ tiền và không tạo giao dịch thanh toán.')
    post('/internal/otp', {'transaction_id': transaction_id, 'user_id': 1, 'student_id': '524H0106', 'amount': 1000})
    print('Đã xếp hàng email. Mở Mailpit http://127.0.0.1:8025 và tìm giao dịch', transaction_id)
    code = input('Nhập OTP gồm 6 chữ số từ email: ').strip()
    result = post('/internal/otp/verify', {'transaction_id': transaction_id, 'user_id': 1, 'otp': code})
    print('Xác thực OTP thành công.' if result.get('verified') else 'Chưa xác thực thành công.')
    print('Số dư không thay đổi. Đây là kiểm tra Notification Service, chưa phải thanh toán học phí.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print('Không hoàn tất kiểm tra:', str(error))
        raise SystemExit(1)
