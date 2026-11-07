import hashlib
import hmac
import os
import re
import secrets
import time
from common import ApiError, Handler, database, initialize, integer_value, remote, require_internal, serve, text_value, transaction_id

PAYMENT_URL = os.environ.get('PAYMENT_URL', 'http://127.0.0.1:8002')
SESSION_TTL = 3600


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 600000).hex()


def initialize_users():
    initialize('users', 'user_schema.sql')
    accounts = [(1, 'linh', 'Phan Trường Nhất Linh', '0900000001', os.environ.get('DEMO_LINH_EMAIL', 'linh@example.com')),
                (2, 'minh', 'Tài khoản kiểm thử thứ hai', '0900000002', os.environ.get('DEMO_SECOND_EMAIL', 'minh@example.com'))]
    with database('users') as con:
        for uid, username, name, phone, email in accounts:
            if con.execute('SELECT 1 FROM users WHERE id=?', (uid,)).fetchone():
                continue
            if not re.fullmatch(r'[^\s@\r\n]+@[^\s@\r\n]+\.[^\s@\r\n]+', email):
                raise RuntimeError('Địa chỉ email demo không hợp lệ.')
            salt = secrets.token_hex(16)
            con.execute('INSERT OR IGNORE INTO users VALUES(?,?,?,?,?,?,?,?)',
                (uid, username, hash_password('Demo@123', salt), salt, name, phone, email, 30000000))


def auth_user(headers):
    authorization = headers.get('Authorization', '')
    if not authorization.startswith('Bearer '):
        raise ApiError(401, 'UNAUTHORIZED', 'Vui lòng đăng nhập.')
    digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
    with database('users') as con:
        user = con.execute('SELECT u.id,u.username,u.full_name,u.phone,u.email,u.balance FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?', (digest, int(time.time()))).fetchone()
    if not user:
        raise ApiError(401, 'SESSION_EXPIRED', 'Phiên đăng nhập đã hết hạn hoặc không hợp lệ.')
    return dict(user)


def login(body, ip):
    username = text_value(body, 'username', maximum=64).strip()
    password = text_value(body, 'password', maximum=256)
    now = int(time.time())
    with database('users') as con:
        row = con.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
    salt = row['salt'] if row else '0' * 32
    valid = hmac.compare_digest(hash_password(password, salt), row['password_hash'] if row else '0' * 64)
    with database('users') as con:
        con.execute('BEGIN IMMEDIATE')
        attempt = con.execute('SELECT * FROM login_attempts WHERE client_key=?', (ip,)).fetchone()
        if attempt and attempt['failures'] >= 5 and attempt['expires_at'] > now:
            con.rollback()
            raise ApiError(429, 'LOGIN_LOCKED', 'Đăng nhập sai quá nhiều lần. Thử lại sau 60 giây.')
        if not row or not valid:
            failures = attempt['failures'] + 1 if attempt and attempt['expires_at'] > now else 1
            con.execute('INSERT OR REPLACE INTO login_attempts VALUES(?,?,?)', (ip, failures, now + 60))
            con.commit()
            raise ApiError(401, 'INVALID_CREDENTIALS', 'Sai tên đăng nhập hoặc mật khẩu.')
        con.execute('DELETE FROM login_attempts WHERE client_key=?', (ip,))
        con.execute('DELETE FROM sessions WHERE expires_at<=?', (now,))
        token = secrets.token_urlsafe(32)
        con.execute('INSERT INTO sessions VALUES(?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), row['id'], now + SESSION_TTL))
        con.commit()
    return {'access_token': token, 'token_type': 'Bearer', 'expires_in': SESSION_TTL}


def account_operation(body, refund=False):
    uid = integer_value(body, 'user_id')
    amount = integer_value(body, 'amount')
    tid = transaction_id(body)
    with database('users') as con:
        con.execute('BEGIN IMMEDIATE')
        old = con.execute('SELECT * FROM account_operations WHERE transaction_id=?', (tid,)).fetchone()
        if old and (old['user_id'] != uid or old['amount'] != amount):
            raise ApiError(409, 'IDEMPOTENCY_CONFLICT', 'Mã giao dịch đã gắn với dữ liệu khác.')
        if refund:
            if not old:
                raise ApiError(409, 'DEBIT_NOT_FOUND', 'Không có khoản trừ tiền để hoàn tác.')
            if old['status'] == 'DEBITED':
                con.execute('UPDATE users SET balance=balance+? WHERE id=?', (amount, uid))
                con.execute("UPDATE account_operations SET status='REFUNDED',refunded_at=? WHERE transaction_id=?", (int(time.time()), tid))
        elif old:
            if old['status'] == 'REFUNDED':
                raise ApiError(409, 'ALREADY_REFUNDED', 'Giao dịch đã hoàn tác, không được trừ tiền lại.')
        else:
            if not con.execute('SELECT 1 FROM users WHERE id=?', (uid,)).fetchone():
                raise ApiError(404, 'USER_NOT_FOUND', 'Không tìm thấy tài khoản.')
            changed = con.execute('UPDATE users SET balance=balance-? WHERE id=? AND balance>=?', (amount, uid, amount)).rowcount
            if not changed:
                raise ApiError(409, 'INSUFFICIENT_BALANCE', 'Số dư không đủ tại thời điểm thanh toán.')
            con.execute('INSERT INTO account_operations VALUES(?,?,?,?,?,NULL)', (tid, uid, amount, 'DEBITED', int(time.time())))
        balance = con.execute('SELECT balance FROM users WHERE id=?', (uid,)).fetchone()['balance']
        operation = dict(con.execute('SELECT * FROM account_operations WHERE transaction_id=?', (tid,)).fetchone())
        con.commit()
    return (200 if old else 201), {**operation, 'balance': balance}


class API(Handler):
    def route(self):
        if self.path == '/health' and self.command == 'GET':
            return 200, {'status': 'ok', 'service': 'user'}
        if self.path == '/auth/login' and self.command == 'POST':
            return 200, login(self.body, self.client_address[0])
        if self.path.startswith('/internal/'):
            require_internal(self.headers)
            if self.path == '/internal/accounts/debit' and self.command == 'POST':
                return account_operation(self.body)
            if self.path == '/internal/accounts/refund' and self.command == 'POST':
                return account_operation(self.body, refund=True)
            match = re.fullmatch(r'/internal/users/(\d+)', self.path)
            operation = re.fullmatch(r'/internal/accounts/operations/([A-Za-z0-9_-]{3,100})', self.path)
            with database('users') as con:
                if match and self.command == 'GET':
                    row = con.execute('SELECT id,username,full_name,phone,email,balance FROM users WHERE id=?', (int(match[1]),)).fetchone()
                elif operation and self.command == 'GET':
                    row = con.execute('SELECT * FROM account_operations WHERE transaction_id=?', (operation[1],)).fetchone()
                else:
                    raise ApiError(404, 'NOT_FOUND', 'Không tìm thấy API.')
            if not row:
                raise ApiError(404, 'NOT_FOUND', 'Không tìm thấy dữ liệu.')
            return 200, dict(row)
        profile = auth_user(self.headers)
        if self.path in ('/auth/me', '/account') and self.command == 'GET':
            return 200, profile
        if self.path == '/auth/logout' and self.command == 'POST':
            digest = hashlib.sha256(self.headers['Authorization'][7:].encode()).hexdigest()
            with database('users') as con:
                con.execute('DELETE FROM sessions WHERE token_hash=?', (digest,))
            return 200, {'message': 'Đã đăng xuất.'}
        if self.path == '/transactions' and self.command == 'GET':
            from common import SERVICE_KEY
            status, result = remote(PAYMENT_URL, f'/internal/users/{profile["id"]}/transactions', headers={'X-Service-Key': SERVICE_KEY})
            return status, result
        raise ApiError(404, 'NOT_FOUND', 'Không tìm thấy API.')


if __name__ == '__main__':
    initialize_users()
    serve(API, 8001)
