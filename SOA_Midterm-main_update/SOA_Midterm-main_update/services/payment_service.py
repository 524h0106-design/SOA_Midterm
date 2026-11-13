import os
import re
import secrets
import threading
import time
from common import ApiError, Handler, SERVICE_KEY, database, initialize, integer_value, remote, require_internal, serve, text_value

USER_URL = os.environ.get('USER_URL', 'http://127.0.0.1:8001')
NOTIFICATION_URL = os.environ.get('NOTIFICATION_URL', 'http://127.0.0.1:8003')
TUITION_URL = os.environ.get('TUITION_URL', 'http://127.0.0.1:8004')
OTP_TTL = 300


def initialize_payments():
    initialize('payments', 'payment_schema.sql')


def internal_headers():
    return {'X-Service-Key': SERVICE_KEY}


def current_user(headers):
    auth = headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        raise ApiError(401, 'UNAUTHORIZED', 'Vui lòng đăng nhập.')
    status, result = remote(USER_URL, '/auth/me', headers={'Authorization': auth})
    if status != 200:
        err = result.get('error', {}) if isinstance(result, dict) else {}
        raise ApiError(status, err.get('code', 'UNAUTHORIZED'), err.get('message', 'Phiên đăng nhập không hợp lệ.'))
    return result


def expire_overdue_payments(con, now):
    con.execute(
        "UPDATE payments SET status='EXPIRED',failure_code='OTP_EXPIRED',failure_message='Giao dịch đã hết thời hạn xác thực OTP.',updated_at=?,completed_at=? WHERE status='PENDING' AND expires_at<=?",
        (now, now, now)
    )


def serialize_payment(row):
    data = dict(row)
    return {
        'id': data['id'],
        'transaction_id': data['id'],
        'user_id': data['user_id'],
        'payer_name': data['payer_name'],
        'payer_phone': data['payer_phone'],
        'payer_email': data['payer_email'],
        'student_id': data['student_id'],
        'student_name': data['student_name'],
        'semester': data['semester'],
        'amount': data['amount'],
        'status': data['status'],
        'failure_code': data['failure_code'],
        'failure_message': data['failure_message'],
        'debit_completed': bool(data['debit_completed']),
        'tuition_completed': bool(data['tuition_completed']),
        'receipt_queued': bool(data['receipt_queued']),
        'created_at': data['created_at'],
        'expires_at': data['expires_at'],
        'updated_at': data['updated_at'],
        'completed_at': data['completed_at'],
    }


def list_tuitions(headers):
    user = current_user(headers)
    status, data = remote(TUITION_URL, '/internal/tuition', headers=internal_headers())
    if status != 200:
        err = data.get('error', {}) if isinstance(data, dict) else {}
        raise ApiError(status, err.get('code', 'TUITION_LIST_FAILED'), err.get('message', 'Không tải được danh sách sinh viên.'))
    balance = user['balance']
    items = []
    for item in data.get('items', []):
        items.append({
            **item,
            'sufficient_balance': balance >= item['amount'],
            'can_pay': item['status'] == 'UNPAID' and balance >= item['amount'],
        })
    return 200, {'items': items, 'payer_balance': balance}


def lookup_tuition(sid, headers):
    user = current_user(headers)
    status, tuition = remote(TUITION_URL, f'/internal/tuition/{sid}', headers=internal_headers())
    if status != 200:
        err = tuition.get('error', {}) if isinstance(tuition, dict) else {}
        raise ApiError(status, err.get('code', 'TUITION_NOT_FOUND'), err.get('message', 'Không tìm thấy thông tin học phí.'))
    balance = user['balance']
    amount = tuition['amount']
    sufficient = balance >= amount
    can_pay = tuition['status'] == 'UNPAID' and sufficient
    return 200, {
        **tuition,
        'payer': {
            'id': user['id'],
            'full_name': user['full_name'],
            'phone': user['phone'],
            'email': user['email'],
            'balance': balance,
        },
        'payer_balance': balance,
        'sufficient_balance': sufficient,
        'can_pay': can_pay,
    }


def create_payment(body, headers):
    user = current_user(headers)
    sid_input = text_value(body, 'student_id', r'[A-Za-z0-9]{3,30}').upper()
    if 'terms_accepted' in body and body['terms_accepted'] is not True:
        raise ApiError(400, 'TERMS_REQUIRED', 'Bạn cần đồng ý với các thỏa thuận và điều khoản trước khi xác nhận giao dịch.')

    idem_header = headers.get('Idempotency-Key', '').strip()
    idem_key = None
    if idem_header:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', idem_header):
            raise ApiError(400, 'INVALID_IDEMPOTENCY_KEY', 'Idempotency-Key không hợp lệ.')
        idem_key = f'{user["id"]}:{idem_header}'

    now = int(time.time())
    if idem_key:
        with database('payments') as con:
            con.execute('BEGIN IMMEDIATE')
            expire_overdue_payments(con, now)
            existing = con.execute('SELECT * FROM payments WHERE idempotency_key=?', (idem_key,)).fetchone()
            con.commit()
        if existing:
            if existing['student_id'].upper() != sid_input:
                raise ApiError(409, 'IDEMPOTENCY_CONFLICT', 'Idempotency-Key đã gắn với MSSV khác.')
            if existing['status'] in ('PENDING', 'PROCESSING', 'SUCCESS'):
                return 200, serialize_payment(existing)

    t_status, tuition = remote(TUITION_URL, f'/internal/tuition/{sid_input}', headers=internal_headers())
    if t_status != 200:
        err = tuition.get('error', {}) if isinstance(tuition, dict) else {}
        raise ApiError(t_status, err.get('code', 'TUITION_NOT_FOUND'), err.get('message', 'Không tìm thấy khoản học phí.'))

    sid = tuition['student_id']
    amount = tuition['amount']
    if 'amount' in body:
        req_amount = integer_value(body, 'amount')
        if req_amount != amount:
            raise ApiError(400, 'PARTIAL_PAYMENT_NOT_ALLOWED', 'Hệ thống chỉ cho phép thanh toán toàn bộ khoản học phí, không thanh toán một phần.')

    if tuition['status'] == 'PAID':
        raise ApiError(409, 'TUITION_ALREADY_PAID', 'Khoản học phí của sinh viên này đã được thanh toán.')
    if tuition['status'] == 'RESERVED':
        raise ApiError(409, 'TUITION_LOCKED', 'Khoản học phí này đang được xử lý thanh toán. Vui lòng thử lại sau giây lát.')
    if user['balance'] < amount:
        raise ApiError(409, 'INSUFFICIENT_BALANCE', 'Số dư khả dụng của người nộp tiền không đủ để thanh toán khoản học phí này.')

    tid = 'pay-' + secrets.token_hex(12)
    otp_status, otp_res = remote(
        NOTIFICATION_URL,
        '/internal/otp',
        'POST',
        {'transaction_id': tid, 'user_id': user['id'], 'student_id': sid, 'amount': amount, 'ttl_seconds': OTP_TTL},
        internal_headers()
    )
    if otp_status not in (200, 201):
        err = otp_res.get('error', {}) if isinstance(otp_res, dict) else {}
        raise ApiError(otp_status, err.get('code', 'OTP_CREATION_FAILED'), err.get('message', 'Không thể khởi tạo mã xác thực OTP.'))

    expires_at = otp_res.get('expires_at', now + OTP_TTL)
    with database('payments') as con:
        con.execute('BEGIN IMMEDIATE')
        con.execute(
            '''INSERT INTO payments(
                id, idempotency_key, user_id, payer_name, payer_phone, payer_email,
                student_id, student_name, semester, amount, status,
                created_at, expires_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,'PENDING',?,?,?)''',
            (
                tid, idem_key, user['id'], user['full_name'], user['phone'], user['email'],
                sid, tuition['student_name'], tuition['semester'], amount,
                now, expires_at, now
            )
        )
        row = con.execute('SELECT * FROM payments WHERE id=?', (tid,)).fetchone()
        con.commit()
    return 201, serialize_payment(row)


def get_payment(tid, headers):
    user = current_user(headers)
    now = int(time.time())
    with database('payments') as con:
        con.execute('BEGIN IMMEDIATE')
        expire_overdue_payments(con, now)
        row = con.execute('SELECT * FROM payments WHERE id=?', (tid,)).fetchone()
        con.commit()
    if not row or row['user_id'] != user['id']:
        raise ApiError(404, 'PAYMENT_NOT_FOUND', 'Không tìm thấy giao dịch thuộc tài khoản đang đăng nhập.')
    return 200, serialize_payment(row)


def confirm_payment(tid, body, headers):
    user = current_user(headers)
    otp = text_value(body, 'otp', r'[0-9]{6}', maximum=6)
    now = int(time.time())

    with database('payments') as con:
        con.execute('BEGIN IMMEDIATE')
        expire_overdue_payments(con, now)
        row = con.execute('SELECT * FROM payments WHERE id=?', (tid,)).fetchone()
        if not row or row['user_id'] != user['id']:
            con.rollback()
            raise ApiError(404, 'PAYMENT_NOT_FOUND', 'Không tìm thấy giao dịch thuộc tài khoản đang đăng nhập.')
        if row['status'] == 'SUCCESS':
            con.commit()
            return 200, serialize_payment(row)
        if row['status'] == 'PROCESSING':
            con.commit()
            raise ApiError(409, 'PAYMENT_IN_PROGRESS', 'Giao dịch đang được xử lý, vui lòng chờ.')
        if row['status'] == 'EXPIRED':
            con.commit()
            raise ApiError(410, 'OTP_EXPIRED', 'OTP đã hết hạn. Vui lòng tạo giao dịch mới.')
        if row['status'] != 'PENDING':
            con.commit()
            raise ApiError(
                409,
                row['failure_code'] or 'PAYMENT_NOT_PENDING',
                row['failure_message'] or 'Giao dịch đã kết thúc, không thể xác nhận lại.'
            )
        con.execute("UPDATE payments SET status='PROCESSING',updated_at=? WHERE id=?", (now, tid))
        con.commit()

    uid = row['user_id']
    sid = row['student_id']
    amount = row['amount']

    # Bước 1: Xác thực OTP qua Notification Service
    try:
        otp_status, otp_res = remote(
            NOTIFICATION_URL,
            '/internal/otp/verify',
            'POST',
            {'transaction_id': tid, 'user_id': uid, 'otp': otp},
            internal_headers()
        )
    except ApiError as error:
        with database('payments') as con:
            con.execute("UPDATE payments SET status='PENDING',updated_at=? WHERE id=?", (int(time.time()), tid))
        raise error

    if otp_status != 200:
        err = otp_res.get('error', {}) if isinstance(otp_res, dict) else {}
        code = err.get('code', 'OTP_VERIFY_FAILED')
        msg = err.get('message', 'Xác thực OTP không thành công.')
        ts = int(time.time())
        with database('payments') as con:
            if code == 'OTP_INVALID':
                con.execute("UPDATE payments SET status='PENDING',updated_at=? WHERE id=?", (ts, tid))
            elif code == 'OTP_EXPIRED':
                con.execute("UPDATE payments SET status='EXPIRED',failure_code=?,failure_message=?,updated_at=?,completed_at=? WHERE id=?", (code, msg, ts, ts, tid))
            else:
                con.execute("UPDATE payments SET status='FAILED',failure_code=?,failure_message=?,updated_at=?,completed_at=? WHERE id=?", (code, msg, ts, ts, tid))
        raise ApiError(otp_status, code, msg)

    verification_id = otp_res.get('verification_id', '')
    with database('payments') as con:
        con.execute('UPDATE payments SET verification_id=?,updated_at=? WHERE id=?', (verification_id, int(time.time()), tid))

    # Bước 2: Trừ tiền tài khoản người nộp qua User Service (atomic check balance >= amount)
    try:
        debit_status, debit_res = remote(
            USER_URL,
            '/internal/accounts/debit',
            'POST',
            {'transaction_id': tid, 'user_id': uid, 'amount': amount},
            internal_headers()
        )
    except ApiError as error:
        ts = int(time.time())
        with database('payments') as con:
            con.execute(
                "UPDATE payments SET status='FAILED',failure_code=?,failure_message=?,updated_at=?,completed_at=? WHERE id=?",
                (error.code, error.message, ts, ts, tid)
            )
        raise error

    if debit_status not in (200, 201):
        err = debit_res.get('error', {}) if isinstance(debit_res, dict) else {}
        code = err.get('code', 'DEBIT_FAILED')
        msg = err.get('message', 'Không thể trừ tiền tài khoản.')
        ts = int(time.time())
        with database('payments') as con:
            con.execute(
                "UPDATE payments SET status='FAILED',failure_code=?,failure_message=?,updated_at=?,completed_at=? WHERE id=?",
                (code, msg, ts, ts, tid)
            )
        raise ApiError(debit_status, code, msg)

    with database('payments') as con:
        con.execute('UPDATE payments SET debit_completed=1,updated_at=? WHERE id=?', (int(time.time()), tid))

    # Bước 3: Cập nhật trạng thái học phí thành PAID qua Tuition Service
    # Nếu thất bại (ví dụ: tài khoản khác vừa đóng cùng MSSV), thực hiện giao dịch bù trừ (Refund)
    try:
        pay_status, pay_res = remote(
            TUITION_URL,
            f'/internal/tuition/{sid}/pay',
            'POST',
            {'transaction_id': tid, 'user_id': uid, 'amount': amount},
            internal_headers()
        )
    except ApiError as error:
        pay_status = error.status
        pay_res = {'error': {'code': error.code, 'message': error.message}}

    if pay_status not in (200, 201):
        err = pay_res.get('error', {}) if isinstance(pay_res, dict) else {}
        code = err.get('code', 'TUITION_UPDATE_FAILED')
        msg = err.get('message', 'Không thể cập nhật trạng thái học phí.')
        refunded = False
        try:
            ref_status, _ = remote(
                USER_URL,
                '/internal/accounts/refund',
                'POST',
                {'transaction_id': tid, 'user_id': uid, 'amount': amount},
                internal_headers()
            )
            refunded = ref_status == 200
        except ApiError:
            refunded = False

        ts = int(time.time())
        full_msg = f'{msg} Số tiền đã được hoàn trả vào tài khoản.' if refunded else f'{msg} Hệ thống đang tự động hoàn trả số tiền.'
        with database('payments') as con:
            con.execute(
                "UPDATE payments SET status='FAILED',needs_refund=?,failure_code=?,failure_message=?,updated_at=?,completed_at=? WHERE id=?",
                (0 if refunded else 1, code, full_msg, ts, ts, tid)
            )
        raise ApiError(pay_status, code, full_msg)

    # Bước 4: Lưu kết quả giao dịch thành công vào lịch sử
    completed_ts = int(time.time())
    with database('payments') as con:
        con.execute(
            "UPDATE payments SET status='SUCCESS',tuition_completed=1,updated_at=?,completed_at=? WHERE id=?",
            (completed_ts, completed_ts, tid)
        )

    # Bước 5: Gửi email xác nhận giao dịch thành công qua Notification Service
    try:
        rec_status, _ = remote(
            NOTIFICATION_URL,
            '/internal/receipts',
            'POST',
            {'transaction_id': tid, 'user_id': uid, 'student_id': sid, 'amount': amount},
            internal_headers()
        )
        if rec_status in (200, 202):
            with database('payments') as con:
                con.execute('UPDATE payments SET receipt_queued=1,updated_at=? WHERE id=?', (int(time.time()), tid))
    except ApiError:
        pass

    with database('payments') as con:
        final_row = con.execute('SELECT * FROM payments WHERE id=?', (tid,)).fetchone()
    return 200, serialize_payment(final_row)


def user_transactions(uid):
    now = int(time.time())
    with database('payments') as con:
        con.execute('BEGIN IMMEDIATE')
        expire_overdue_payments(con, now)
        rows = con.execute('SELECT * FROM payments WHERE user_id=? ORDER BY created_at DESC, rowid DESC', (uid,)).fetchall()
        con.commit()
    return 200, {'items': [serialize_payment(r) for r in rows]}


def reconcile_once():
    now = int(time.time())
    with database('payments') as con:
        con.execute('BEGIN IMMEDIATE')
        expire_overdue_payments(con, now)
        refund_rows = con.execute('SELECT * FROM payments WHERE needs_refund=1 LIMIT 5').fetchall()
        receipt_rows = con.execute("SELECT * FROM payments WHERE status='SUCCESS' AND receipt_queued=0 LIMIT 5").fetchall()
        con.commit()

    for row in refund_rows:
        try:
            status, _ = remote(
                USER_URL,
                '/internal/accounts/refund',
                'POST',
                {'transaction_id': row['id'], 'user_id': row['user_id'], 'amount': row['amount']},
                internal_headers()
            )
            if status == 200:
                with database('payments') as con:
                    con.execute('UPDATE payments SET needs_refund=0,updated_at=? WHERE id=?', (int(time.time()), row['id']))
        except ApiError:
            pass

    for row in receipt_rows:
        try:
            status, _ = remote(
                NOTIFICATION_URL,
                '/internal/receipts',
                'POST',
                {'transaction_id': row['id'], 'user_id': row['user_id'], 'student_id': row['student_id'], 'amount': row['amount']},
                internal_headers()
            )
            if status in (200, 202):
                with database('payments') as con:
                    con.execute('UPDATE payments SET receipt_queued=1,updated_at=? WHERE id=?', (int(time.time()), row['id']))
        except ApiError:
            pass


def reconcile_forever():
    while True:
        try:
            reconcile_once()
        except Exception as error:
            print('Payment reconcile error:', type(error).__name__, flush=True)
        time.sleep(1.0)


class API(Handler):
    def route(self):
        if self.path == '/health' and self.command == 'GET':
            return 200, {'status': 'ok', 'service': 'payment'}

        if self.path.startswith('/internal/'):
            require_internal(self.headers)
            history_match = re.fullmatch(r'/internal/users/(\d+)/transactions', self.path)
            if history_match and self.command == 'GET':
                return user_transactions(int(history_match[1]))
            raise ApiError(404, 'NOT_FOUND', 'Không tìm thấy API nội bộ.')

        if self.path == '/tuition' and self.command == 'GET':
            return list_tuitions(self.headers)

        tuition_match = re.fullmatch(r'/tuition/([A-Za-z0-9]{3,30})', self.path)
        if tuition_match and self.command == 'GET':
            return lookup_tuition(tuition_match[1], self.headers)

        if self.path == '/payments' and self.command == 'POST':
            return create_payment(self.body, self.headers)

        payment_match = re.fullmatch(r'/payments/([A-Za-z0-9_-]{3,100})', self.path)
        if payment_match and self.command == 'GET':
            return get_payment(payment_match[1], self.headers)

        confirm_match = re.fullmatch(r'/payments/([A-Za-z0-9_-]{3,100})/confirm', self.path)
        if confirm_match and self.command == 'POST':
            return confirm_payment(confirm_match[1], self.body, self.headers)

        raise ApiError(404, 'NOT_FOUND', 'Không tìm thấy API thanh toán.')


if __name__ == '__main__':
    initialize_payments()
    threading.Thread(target=reconcile_forever, daemon=True).start()
    serve(API, 8002)
