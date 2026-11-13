import email
import json
import os
import re
import secrets
import socket
import socketserver
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def unused_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def request(base, path, body=None, headers=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=15) as result:
            return result.status, json.load(result)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def wait_until(condition, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(.05)
    raise AssertionError('Timed out waiting for service or email')


class MailState:
    messages = []
    lock = threading.Lock()
    failures = 0
    reject_transaction = ''


class SMTPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b'220 integration-test SMTP\r\n')
        while True:
            line = self.rfile.readline()
            if not line:
                return
            command = line.decode().strip().upper()
            if command.startswith(('EHLO', 'HELO')):
                self.wfile.write(b'250 localhost\r\n')
            elif command.startswith(('MAIL', 'RCPT', 'RSET', 'NOOP')):
                self.wfile.write(b'250 OK\r\n')
            elif command == 'DATA':
                self.wfile.write(b'354 End with dot\r\n')
                content = []
                while True:
                    part = self.rfile.readline()
                    if not part or part == b'.\r\n':
                        break
                    content.append(part[1:] if part.startswith(b'..') else part)
                with MailState.lock:
                    parsed = email.message_from_bytes(b''.join(content))
                    decoded = parsed.get_payload(decode=True).decode('utf-8')
                    failed = MailState.failures > 0 and MailState.reject_transaction in decoded
                    if failed:
                        MailState.failures -= 1
                    else:
                        MailState.messages.append(parsed)
                self.wfile.write(b'451 Temporary failure\r\n' if failed else b'250 Accepted\r\n')
            elif command == 'QUIT':
                self.wfile.write(b'221 Bye\r\n')
                return
            else:
                self.wfile.write(b'502 Unsupported\r\n')


class SMTPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class PaymentContractFixture(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        match = re.fullmatch(r'/internal/users/(\d+)/transactions', self.path)
        if self.headers.get('X-Service-Key') != self.server.service_key:
            status, result = 403, {'error': {'code': 'FORBIDDEN', 'message': 'Forbidden'}}
        elif match:
            status, result = 200, {'items': [{'id': 'fixture-' + match[1], 'student_id': '524H0106', 'amount': 1000000,
                                            'status': 'SUCCESS', 'created_at': 1770000000, 'completed_at': 1770000001}]}
        else:
            status, result = 404, {'error': {'code': 'NOT_FOUND', 'message': 'Not found'}}
        body = json.dumps(result).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ServicesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.data = Path(cls.temp.name)
        cls.key = secrets.token_urlsafe(48)
        cls.internal = {'X-Service-Key': cls.key}
        cls.smtp = SMTPServer(('127.0.0.1', 0), SMTPHandler)
        cls.payment_fixture = ThreadingHTTPServer(('127.0.0.1', 0), PaymentContractFixture)
        cls.payment_fixture.service_key = cls.key
        for server in (cls.smtp, cls.payment_fixture):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.ports = {name: unused_port() for name in ('user', 'notification', 'gateway')}
        cls.user = f'http://127.0.0.1:{cls.ports["user"]}'
        cls.notification = f'http://127.0.0.1:{cls.ports["notification"]}'
        cls.gateway = f'http://127.0.0.1:{cls.ports["gateway"]}'
        cls.env = {**os.environ, 'SERVICE_KEY': cls.key, 'DATA_DIR': str(cls.data), 'USER_URL': cls.user,
                   'PAYMENT_URL': f'http://127.0.0.1:{cls.payment_fixture.server_port}',
                   'SMTP_HOST': '127.0.0.1', 'SMTP_PORT': str(cls.smtp.server_address[1]),
                   'SMTP_SECURITY': 'none', 'SMTP_USER': '', 'BIND_HOST': '127.0.0.1',
                   'DEMO_LINH_EMAIL': 'linh@example.com', 'DEMO_SECOND_EMAIL': 'minh@example.com'}
        cls.processes = {}
        for name, filename in [('user', 'user_service.py'), ('notification', 'notification_service.py'), ('gateway', 'gateway.py')]:
            cls.processes[name] = subprocess.Popen([sys.executable, str(ROOT / 'services' / filename)],
                env={**cls.env, 'PORT': str(cls.ports[name])}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def ready(base, path):
            try:
                return request(base, path)[0] in (200, 401)
            except OSError:
                return False
        wait_until(lambda: ready(cls.user, '/health'))
        wait_until(lambda: ready(cls.notification, '/health'))
        wait_until(lambda: ready(cls.gateway, '/api/auth/me'))
        cls.auth = {}
        for username in ['linh', 'minh']:
            status, result = request(cls.gateway, '/api/auth/login', {'username': username, 'password': 'Demo@123'})
            if status != 200:
                raise AssertionError(result)
            cls.auth[username] = {'Authorization': 'Bearer ' + result['access_token']}

    @classmethod
    def tearDownClass(cls):
        for process in cls.processes.values():
            process.terminate()
        for process in cls.processes.values():
            process.wait(timeout=5)
        for server in [cls.smtp, cls.payment_fixture]:
            server.shutdown()
            server.server_close()
        cls.temp.cleanup()

    def setUp(self):
        from contextlib import closing
        with closing(sqlite3.connect(self.data / 'users.db')) as con:
            con.execute('DELETE FROM account_operations')
            con.execute('UPDATE users SET balance=30000000')
            con.execute('DELETE FROM login_attempts')
            con.commit()

    def tid(self):
        return uuid.uuid4().hex

    def operation(self, tid=None, user=1, amount=20000000):
        return {'transaction_id': tid or self.tid(), 'user_id': user, 'amount': amount}

    def create(self, ttl=300, user=1, amount=18500000):
        body = {**self.operation(user=user, amount=amount), 'student_id': '524H0106', 'ttl_seconds': ttl}
        status, result = request(self.notification, '/internal/otp', body, self.internal)
        self.assertEqual(status, 201, result)
        self.assertNotIn('otp', result)
        self.assertNotIn('code_hash', result)
        return body, result

    def find_mail(self, tid):
        with MailState.lock:
            for message in MailState.messages:
                content = message.get_payload(decode=True).decode('utf-8')
                if tid in content:
                    return message, content

    def code(self, tid):
        _, content = wait_until(lambda: self.find_mail(tid))
        return re.search(r'OTP: (\d{6})', content)[1]

    def verify(self, body, code):
        return request(self.notification, '/internal/otp/verify', {'transaction_id': body['transaction_id'], 'user_id': body['user_id'], 'otp': code}, self.internal)

    def test_login_profile_and_no_password_exposure(self):
        status, profile = request(self.gateway, '/api/auth/me', headers=self.auth['linh'])
        self.assertEqual(status, 200)
        self.assertEqual(profile['full_name'], 'Phan Trường Nhất Linh')
        self.assertEqual(profile['balance'], 30000000)
        self.assertNotIn('password_hash', profile)
        self.assertNotIn('salt', profile)
        self.assertEqual(request(self.user, '/auth/me')[0], 401)
        self.assertEqual(request(self.user, '/auth/me', headers={'Authorization': 'Bearer fabricated'})[0], 401)

    def test_login_lock_and_invalid_input(self):
        for _ in range(5):
            self.assertEqual(request(self.gateway, '/api/auth/login', {'username': 'linh', 'password': 'incorrect'})[0], 401)
        self.assertEqual(request(self.gateway, '/api/auth/login', {'username': 'linh', 'password': 'Demo@123'})[0], 429)
        self.assertEqual(request(self.user, '/auth/login', {'username': ['linh'], 'password': 123})[0], 400)

    def test_logout_revokes_session(self):
        status, data = request(self.user, '/auth/login', {'username': 'linh', 'password': 'Demo@123'})
        self.assertEqual(status, 200)
        headers = {'Authorization': 'Bearer ' + data['access_token']}
        self.assertEqual(request(self.user, '/auth/logout', {}, headers)[0], 200)
        self.assertEqual(request(self.user, '/auth/me', headers=headers)[0], 401)

    def test_session_expiration(self):
        _, data = request(self.user, '/auth/login', {'username': 'minh', 'password': 'Demo@123'})
        import hashlib
        with sqlite3.connect(self.data / 'users.db') as con:
            con.execute('UPDATE sessions SET expires_at=0 WHERE token_hash=?', (hashlib.sha256(data['access_token'].encode()).hexdigest(),))
        self.assertEqual(request(self.user, '/auth/me', headers={'Authorization': 'Bearer ' + data['access_token']})[0], 401)

    def test_internal_apis_not_public(self):
        body = self.operation()
        self.assertEqual(request(self.user, '/internal/accounts/debit', body)[0], 403)
        self.assertEqual(request(self.notification, '/internal/otp', {**body, 'student_id': '524H0106'})[0], 403)
        self.assertEqual(request(self.gateway, '/api/internal/accounts/debit', body, self.auth['linh'])[0], 404)

    def test_concurrent_debits_never_overdraw(self):
        bodies = [self.operation() for _ in range(8)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda b: request(self.user, '/internal/accounts/debit', b, self.internal), bodies))
        self.assertEqual(sum(status == 201 for status, _ in results), 1)
        self.assertEqual(sum(status == 409 for status, _ in results), 7)
        self.assertEqual(request(self.user, '/account', headers=self.auth['linh'])[1]['balance'], 10000000)

    def test_idempotent_concurrent_debit_and_payload_conflict(self):
        body = self.operation(amount=18500000)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: request(self.user, '/internal/accounts/debit', body, self.internal), range(8)))
        self.assertEqual(sum(status == 201 for status, _ in results), 1)
        self.assertTrue(all(status in (200, 201) for status, _ in results))
        self.assertEqual(request(self.user, '/account', headers=self.auth['linh'])[1]['balance'], 11500000)
        self.assertEqual(request(self.user, '/internal/accounts/debit', {**body, 'amount': 1}, self.internal)[0], 409)
        self.assertEqual(request(self.user, '/internal/accounts/debit', {**body, 'user_id': 2}, self.internal)[0], 409)

    def test_idempotent_refund_and_no_redebit(self):
        body = self.operation()
        self.assertEqual(request(self.user, '/internal/accounts/debit', body, self.internal)[0], 201)
        for _ in range(3):
            self.assertEqual(request(self.user, '/internal/accounts/refund', body, self.internal)[0], 200)
        self.assertEqual(request(self.user, '/account', headers=self.auth['linh'])[1]['balance'], 30000000)
        self.assertEqual(request(self.user, '/internal/accounts/debit', body, self.internal)[0], 409)
        self.assertEqual(request(self.user, '/internal/accounts/refund', self.operation(), self.internal)[0], 409)

    def test_amount_validation(self):
        for value in [-1, 0, 1.5, True, '1000', None]:
            self.assertEqual(request(self.user, '/internal/accounts/debit', {**self.operation(), 'amount': value}, self.internal)[0], 400)

    def test_otp_email_scope_and_single_use_under_concurrency(self):
        body, _ = self.create()
        code = self.code(body['transaction_id'])
        message, _ = self.find_mail(body['transaction_id'])
        self.assertEqual(message['To'], 'linh@example.com')
        self.assertEqual(self.verify({**body, 'user_id': 2}, code)[0], 404)
        self.assertEqual(self.verify({**body, 'transaction_id': self.tid()}, code)[0], 404)
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.verify(body, code), range(6)))
        self.assertEqual(sum(status == 200 for status, _ in results), 1)
        self.assertEqual(sum(status == 409 for status, _ in results), 5)
        grant = next(result for status, result in results if status == 200)
        self.assertEqual(grant['amount'], body['amount'])
        recovered = request(self.notification, '/internal/otp/' + body['transaction_id'] + '/status', headers=self.internal)[1]
        self.assertEqual(recovered['verification_id'], grant['verification_id'])

    def test_otp_expiration_and_maximum_ttl(self):
        body, _ = self.create()
        code = self.code(body['transaction_id'])
        with sqlite3.connect(self.data / 'notifications.db') as con:
            con.execute('UPDATE otp_challenges SET expires_at=0 WHERE transaction_id=?', (body['transaction_id'],))
        self.assertEqual(self.verify(body, code)[0], 410)
        self.assertEqual(request(self.notification, '/internal/otp', {**body, 'transaction_id': self.tid(), 'ttl_seconds': 301}, self.internal)[0], 400)

    def test_otp_lock_after_five_wrong_codes(self):
        body, _ = self.create()
        correct = self.code(body['transaction_id'])
        wrong = '000000' if correct != '000000' else '999999'
        for _ in range(4):
            self.assertEqual(self.verify(body, wrong)[0], 400)
        self.assertEqual(self.verify(body, wrong)[0], 429)
        self.assertEqual(self.verify(body, correct)[0], 429)

    def test_otp_creation_idempotency_and_unique_active_hashes(self):
        body, first = self.create()
        status, repeated = request(self.notification, '/internal/otp', body, self.internal)
        self.assertEqual(status, 200)
        self.assertEqual(first['expires_at'], repeated['expires_at'])
        self.assertEqual(request(self.notification, '/internal/otp', {**body, 'amount': 1}, self.internal)[0], 409)
        bodies = [{**self.operation(amount=1000), 'student_id': '524H0106'} for _ in range(12)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda b: request(self.notification, '/internal/otp', b, self.internal), bodies))
        self.assertTrue(all(status == 201 for status, _ in results))
        with sqlite3.connect(self.data / 'notifications.db') as con:
            total, distinct = con.execute("SELECT count(*),count(DISTINCT code_hash) FROM otp_challenges WHERE status='ACTIVE'").fetchone()
        self.assertEqual(total, distinct)

    def test_receipt_retry_and_duplicate_request(self):
        body = {**self.operation(amount=18500000), 'student_id': '524H0106'}
        with MailState.lock:
            MailState.failures = 1
            MailState.reject_transaction = body['transaction_id']
        self.assertEqual(request(self.notification, '/internal/receipts', body, self.internal)[0], 202)
        self.assertEqual(request(self.notification, '/internal/receipts', body, self.internal)[0], 200)
        self.assertEqual(request(self.notification, '/internal/receipts', {**body, 'amount': 100}, self.internal)[0], 409)
        wait_until(lambda: self.find_mail(body['transaction_id']), timeout=20)
        state = wait_until(lambda: (lambda r: r if r.get('status') == 'SENT' else None)(request(self.notification, '/internal/outbox/receipt-' + body['transaction_id'], headers=self.internal)[1]))
        self.assertGreaterEqual(state['attempts'], 2)
        with MailState.lock:
            matches = [m for m in MailState.messages if body['transaction_id'] in m.get_payload(decode=True).decode('utf-8')]
        self.assertEqual(len(matches), 1)

    def test_history_is_scoped_to_logged_in_user(self):
        for username, uid in [('linh', 1), ('minh', 2)]:
            status, result = request(self.gateway, '/api/transactions', headers=self.auth[username])
            self.assertEqual(status, 200)
            self.assertEqual(result['items'][0]['id'], f'fixture-{uid}')
        self.assertEqual(request(self.gateway, '/api/transactions')[0], 401)

    def test_history_reports_payment_outage(self):
        self.payment_fixture.shutdown()
        self.payment_fixture.server_close()
        try:
            status, result = request(self.gateway, '/api/transactions', headers=self.auth['linh'])
            self.assertEqual(status, 503)
            self.assertEqual(result['error']['code'], 'SERVICE_UNAVAILABLE')
        finally:
            self.__class__.payment_fixture = ThreadingHTTPServer(('127.0.0.1', int(self.env['PAYMENT_URL'].rsplit(':', 1)[1])), PaymentContractFixture)
            self.payment_fixture.service_key = self.key
            threading.Thread(target=self.payment_fixture.serve_forever, daemon=True).start()

    def test_gateway_static_and_no_internal_forwarding(self):
        with urllib.request.urlopen(self.gateway + '/') as response:
            self.assertIn('frame-ancestors', response.headers['Content-Security-Policy'])
            self.assertIn('Xác nhận bằng OTP', response.read().decode())
        self.assertEqual(request(self.gateway, '/api/unknown', headers=self.auth['linh'])[0], 404)

    def test_persistence_across_user_restart(self):
        body = self.operation(amount=5000000)
        self.assertEqual(request(self.user, '/internal/accounts/debit', body, self.internal)[0], 201)
        process = self.processes['user']
        process.terminate()
        process.wait(timeout=5)
        self.processes['user'] = subprocess.Popen([sys.executable, str(ROOT / 'services' / 'user_service.py')],
            env={**self.env, 'PORT': str(self.ports['user'])}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def ready():
            try:
                return request(self.user, '/auth/me', headers=self.auth['linh'])
            except OSError:
                return None
        status, result = wait_until(ready)
        self.assertEqual(status, 200)
        self.assertEqual(result['balance'], 25000000)
        self.assertEqual(request(self.user, '/internal/accounts/debit', body, self.internal)[0], 200)


class PaymentAndTuitionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.data = Path(cls.temp.name)
        cls.key = secrets.token_urlsafe(48)
        cls.internal = {'X-Service-Key': cls.key}
        cls.smtp = SMTPServer(('127.0.0.1', 0), SMTPHandler)
        threading.Thread(target=cls.smtp.serve_forever, daemon=True).start()
        cls.ports = {name: unused_port() for name in ('user', 'tuition', 'notification', 'payment', 'gateway')}
        cls.user = f'http://127.0.0.1:{cls.ports["user"]}'
        cls.tuition = f'http://127.0.0.1:{cls.ports["tuition"]}'
        cls.notification = f'http://127.0.0.1:{cls.ports["notification"]}'
        cls.payment = f'http://127.0.0.1:{cls.ports["payment"]}'
        cls.gateway = f'http://127.0.0.1:{cls.ports["gateway"]}'
        cls.env = {
            **os.environ,
            'SERVICE_KEY': cls.key,
            'DATA_DIR': str(cls.data),
            'USER_URL': cls.user,
            'TUITION_URL': cls.tuition,
            'NOTIFICATION_URL': cls.notification,
            'PAYMENT_URL': cls.payment,
            'SMTP_HOST': '127.0.0.1',
            'SMTP_PORT': str(cls.smtp.server_address[1]),
            'SMTP_SECURITY': 'none',
            'SMTP_USER': '',
            'BIND_HOST': '127.0.0.1',
            'DEMO_LINH_EMAIL': 'linh@example.com',
            'DEMO_SECOND_EMAIL': 'minh@example.com',
        }
        cls.processes = {}
        for name, filename in [
            ('user', 'user_service.py'),
            ('tuition', 'tuition_service.py'),
            ('notification', 'notification_service.py'),
            ('payment', 'payment_service.py'),
            ('gateway', 'gateway.py'),
        ]:
            cls.processes[name] = subprocess.Popen(
                [sys.executable, str(ROOT / 'services' / filename)],
                env={**cls.env, 'PORT': str(cls.ports[name])},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        def ready(base, path):
            try:
                return request(base, path)[0] in (200, 401)
            except OSError:
                return False

        for svc in (cls.user, cls.tuition, cls.notification, cls.payment):
            wait_until(lambda s=svc: ready(s, '/health'))
        wait_until(lambda: ready(cls.gateway, '/api/auth/me'))

        cls.auth = {}
        for username in ['linh', 'minh']:
            status, result = request(cls.gateway, '/api/auth/login', {'username': username, 'password': 'Demo@123'})
            if status != 200:
                raise AssertionError(result)
            cls.auth[username] = {'Authorization': 'Bearer ' + result['access_token']}

    @classmethod
    def tearDownClass(cls):
        for process in cls.processes.values():
            process.terminate()
        for process in cls.processes.values():
            process.wait(timeout=5)
        cls.smtp.shutdown()
        cls.smtp.server_close()
        cls.temp.cleanup()

    def setUp(self):
        from contextlib import closing
        with closing(sqlite3.connect(self.data / 'users.db')) as con:
            con.execute('DELETE FROM account_operations')
            con.execute('UPDATE users SET balance=30000000')
            con.commit()
        with closing(sqlite3.connect(self.data / 'tuitions.db')) as con:
            con.execute("UPDATE tuitions SET status='UNPAID',reserved_by=NULL,reserved_until=0,paid_transaction_id=NULL,paid_by_user_id=NULL,paid_at=NULL WHERE student_id NOT IN ('524H0105', '524H0110')")
            con.commit()
        with closing(sqlite3.connect(self.data / 'payments.db')) as con:
            con.execute('DELETE FROM payments')
            con.commit()

    def find_mail(self, tid, keyword=None):
        with MailState.lock:
            for message in MailState.messages:
                content = message.get_payload(decode=True).decode('utf-8')
                if tid in content and (keyword is None or keyword in content):
                    return message, content

    def get_otp_code(self, tid):
        _, content = wait_until(lambda: self.find_mail(tid, 'OTP:'))
        return re.search(r'OTP: (\d{6})', content)[1]

    def test_tuition_not_found(self):
        status, list_res = request(self.gateway, '/api/tuition', headers=self.auth['linh'])
        self.assertEqual(status, 200)
        self.assertEqual(len(list_res['items']), 10)
        status, res = request(self.gateway, '/api/tuition/524H9999', headers=self.auth['linh'])
        self.assertEqual(status, 404)
        self.assertEqual(res['error']['code'], 'TUITION_NOT_FOUND')
        status, res = request(self.gateway, '/api/payments', {'student_id': '524H9999', 'terms_accepted': True}, self.auth['linh'])
        self.assertEqual(status, 404)
        self.assertEqual(res['error']['code'], 'TUITION_NOT_FOUND')

    def test_tuition_already_paid(self):
        status, res = request(self.gateway, '/api/tuition/524H0110', headers=self.auth['linh'])
        self.assertEqual(status, 200)
        self.assertEqual(res['status'], 'PAID')
        self.assertFalse(res['can_pay'])
        status, res = request(self.gateway, '/api/payments', {'student_id': '524H0110', 'terms_accepted': True}, self.auth['linh'])
        self.assertEqual(status, 409)
        self.assertEqual(res['error']['code'], 'TUITION_ALREADY_PAID')

    def test_insufficient_balance_and_partial_payment_rejected(self):
        status, res = request(self.gateway, '/api/tuition/524H0109', headers=self.auth['linh'])
        self.assertEqual(status, 200)
        self.assertEqual(res['amount'], 35000000)
        self.assertFalse(res['sufficient_balance'])
        self.assertFalse(res['can_pay'])
        status, res = request(self.gateway, '/api/payments', {'student_id': '524H0109', 'terms_accepted': True}, self.auth['linh'])
        self.assertEqual(status, 409)
        self.assertEqual(res['error']['code'], 'INSUFFICIENT_BALANCE')

        status, res = request(self.gateway, '/api/payments', {'student_id': '524H0106', 'amount': 5000000, 'terms_accepted': True}, self.auth['linh'])
        self.assertEqual(status, 400)
        self.assertEqual(res['error']['code'], 'PARTIAL_PAYMENT_NOT_ALLOWED')

        status, res = request(self.gateway, '/api/payments', {'student_id': '524H0106', 'terms_accepted': False}, self.auth['linh'])
        self.assertEqual(status, 400)
        self.assertEqual(res['error']['code'], 'TERMS_REQUIRED')

    def test_full_payment_success_flow_and_receipt(self):
        status, tuition = request(self.gateway, '/api/tuition/524H0106', headers=self.auth['linh'])
        self.assertEqual(status, 200)
        self.assertTrue(tuition['can_pay'])

        status, created = request(self.gateway, '/api/payments', {'student_id': '524H0106', 'terms_accepted': True}, self.auth['linh'])
        self.assertEqual(status, 201, created)
        tid = created['id']
        self.assertEqual(created['status'], 'PENDING')

        status, fetched = request(self.gateway, f'/api/payments/{tid}', headers=self.auth['linh'])
        self.assertEqual(status, 200)
        self.assertEqual(fetched['student_id'], '524H0106')
        self.assertEqual(request(self.gateway, f'/api/payments/{tid}', headers=self.auth['minh'])[0], 404)

        otp = self.get_otp_code(tid)
        wrong_otp = '000000' if otp != '000000' else '999999'
        status, err = request(self.gateway, f'/api/payments/{tid}/confirm', {'otp': wrong_otp}, self.auth['linh'])
        self.assertEqual(status, 400)
        self.assertEqual(err['error']['code'], 'OTP_INVALID')

        status, confirmed = request(self.gateway, f'/api/payments/{tid}/confirm', {'otp': otp}, self.auth['linh'])
        self.assertEqual(status, 200, confirmed)
        self.assertEqual(confirmed['status'], 'SUCCESS')

        _, account = request(self.gateway, '/api/account', headers=self.auth['linh'])
        self.assertEqual(account['balance'], 11500000)

        _, tuition_after = request(self.gateway, '/api/tuition/524H0106', headers=self.auth['linh'])
        self.assertEqual(tuition_after['status'], 'PAID')
        self.assertEqual(tuition_after['paid_transaction_id'], tid)

        status, history = request(self.gateway, '/api/transactions', headers=self.auth['linh'])
        self.assertEqual(status, 200)
        self.assertEqual(len(history['items']), 1)
        self.assertEqual(history['items'][0]['id'], tid)
        self.assertEqual(history['items'][0]['status'], 'SUCCESS')

        wait_until(lambda: self.find_mail(tid, 'Đã thanh toán toàn bộ'))

    def test_concurrent_transactions_same_account_never_overdraw(self):
        # Tài khoản linh có 30tr. Khởi tạo 2 giao dịch: 524H0106 (18.5tr) và 524H0107 (15tr). Tổng = 33.5tr > 30tr.
        s1, p1 = request(self.gateway, '/api/payments', {'student_id': '524H0106', 'terms_accepted': True}, self.auth['linh'])
        s2, p2 = request(self.gateway, '/api/payments', {'student_id': '524H0107', 'terms_accepted': True}, self.auth['linh'])
        self.assertEqual(s1, 201)
        self.assertEqual(s2, 201)
        otp1 = self.get_otp_code(p1['id'])
        otp2 = self.get_otp_code(p2['id'])

        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(request, self.gateway, f'/api/payments/{p1["id"]}/confirm', {'otp': otp1}, self.auth['linh'])
            f2 = pool.submit(request, self.gateway, f'/api/payments/{p2["id"]}/confirm', {'otp': otp2}, self.auth['linh'])
            results = [f1.result(), f2.result()]

        statuses = [st for st, _ in results]
        self.assertEqual(statuses.count(200), 1, results)
        self.assertEqual(statuses.count(409), 1, results)

        failed = next(res for st, res in results if st == 409)
        self.assertEqual(failed['error']['code'], 'INSUFFICIENT_BALANCE')

        _, account = request(self.gateway, '/api/account', headers=self.auth['linh'])
        self.assertIn(account['balance'], (11500000, 15000000))
        self.assertGreaterEqual(account['balance'], 0)

        _, t1 = request(self.gateway, '/api/tuition/524H0106', headers=self.auth['linh'])
        _, t2 = request(self.gateway, '/api/tuition/524H0107', headers=self.auth['linh'])
        self.assertEqual([t1['status'], t2['status']].count('PAID'), 1)
        self.assertEqual([t1['status'], t2['status']].count('UNPAID'), 1)

    def test_concurrent_accounts_same_tuition_paid_once_and_refunded(self):
        # Hai tài khoản linh và minh đồng thời thanh toán cho cùng MSSV 524H0108 (12tr)
        s1, p1 = request(self.gateway, '/api/payments', {'student_id': '524H0108', 'terms_accepted': True}, self.auth['linh'])
        s2, p2 = request(self.gateway, '/api/payments', {'student_id': '524H0108', 'terms_accepted': True}, self.auth['minh'])
        self.assertEqual(s1, 201)
        self.assertEqual(s2, 201)
        otp1 = self.get_otp_code(p1['id'])
        otp2 = self.get_otp_code(p2['id'])

        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(request, self.gateway, f'/api/payments/{p1["id"]}/confirm', {'otp': otp1}, self.auth['linh'])
            f2 = pool.submit(request, self.gateway, f'/api/payments/{p2["id"]}/confirm', {'otp': otp2}, self.auth['minh'])
            r1, r2 = f1.result(), f2.result()

        self.assertEqual([r1[0], r2[0]].count(200), 1, (r1, r2))
        self.assertEqual([r1[0], r2[0]].count(409), 1, (r1, r2))

        loser = r1[1] if r1[0] == 409 else r2[1]
        self.assertEqual(loser['error']['code'], 'TUITION_ALREADY_PAID')

        _, acc_linh = request(self.gateway, '/api/account', headers=self.auth['linh'])
        _, acc_minh = request(self.gateway, '/api/account', headers=self.auth['minh'])
        # Một người bị trừ 12tr (còn 18tr), người kia được bảo toàn/hoàn tiền 30tr
        self.assertEqual(sorted([acc_linh['balance'], acc_minh['balance']]), [18000000, 30000000])

        _, tuition = request(self.gateway, '/api/tuition/524H0108', headers=self.auth['linh'])
        self.assertEqual(tuition['status'], 'PAID')
        winner_tid = p1['id'] if r1[0] == 200 else p2['id']
        self.assertEqual(tuition['paid_transaction_id'], winner_tid)


if __name__ == '__main__':
    unittest.main(verbosity=2)
