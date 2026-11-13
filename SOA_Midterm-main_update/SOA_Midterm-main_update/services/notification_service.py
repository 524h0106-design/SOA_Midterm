import hmac
import json
import os
import re
import secrets
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage
from common import ApiError, Handler, SERVICE_KEY, database, fingerprint, initialize, integer_value, remote, require_internal, serve, text_value, transaction_id
from mail_config import load_mail_config

USER_URL = os.environ.get('USER_URL', 'http://127.0.0.1:8001')


def user_contact(uid):
    status, profile = remote(USER_URL, f'/internal/users/{uid}', headers={'X-Service-Key': SERVICE_KEY})
    if status != 200:
        raise ApiError(status, 'USER_LOOKUP_FAILED', 'Không lấy được thông tin người nộp.')
    email = profile.get('email', '')
    if not re.fullmatch(r'[^\s@\r\n]+@[^\s@\r\n]+\.[^\s@\r\n]+', email):
        raise ApiError(422, 'INVALID_EMAIL', 'Email người nộp không hợp lệ.')
    return profile


def queue_email(con, event_id, recipient, subject, body, metadata):
    payload_hash = fingerprint(json.dumps(metadata, sort_keys=True, ensure_ascii=False))
    old = con.execute('SELECT payload_hash FROM email_outbox WHERE event_id=?', (event_id,)).fetchone()
    if old and old['payload_hash'] != payload_hash:
        raise ApiError(409, 'IDEMPOTENCY_CONFLICT', 'Mã email đã được dùng cho dữ liệu khác.')
    if not old:
        con.execute('INSERT INTO email_outbox(event_id,payload_hash,recipient,subject,body) VALUES(?,?,?,?,?)',
                    (event_id, payload_hash, recipient, subject, body))
    return bool(old)


def create_otp(body):
    tid = transaction_id(body)
    uid = integer_value(body, 'user_id')
    amount = integer_value(body, 'amount')
    sid = text_value(body, 'student_id', r'[A-Za-z0-9]{3,30}')
    ttl = integer_value(body, 'ttl_seconds', 1, 300) if 'ttl_seconds' in body else 300
    profile = user_contact(uid)
    now = int(time.time())
    with database('notifications') as con:
        con.execute('BEGIN IMMEDIATE')
        con.execute("UPDATE otp_challenges SET status='EXPIRED' WHERE status='ACTIVE' AND expires_at<=?", (now,))
        old = con.execute('SELECT * FROM otp_challenges WHERE transaction_id=?', (tid,)).fetchone()
        if old:
            if (old['user_id'], old['amount'], old['student_id']) != (uid, amount, sid):
                raise ApiError(409, 'IDEMPOTENCY_CONFLICT', 'Giao dịch đã gắn với thông tin OTP khác.')
            if old['status'] != 'ACTIVE':
                raise ApiError(409, 'OTP_NOT_ACTIVE', 'OTP đã hết hiệu lực; cần tạo giao dịch mới.')
            con.commit()
            return 200, {'transaction_id': tid, 'status': 'ACTIVE', 'expires_at': old['expires_at'], 'email_queued': True}
        while True:
            code = f'{secrets.randbelow(1000000):06d}'
            hashed = fingerprint(code)
            if not con.execute("SELECT 1 FROM otp_challenges WHERE code_hash=? AND status='ACTIVE'", (hashed,)).fetchone():
                break
        expires = now + ttl
        con.execute('INSERT INTO otp_challenges(transaction_id,user_id,student_id,amount,code_hash,status,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?)',
                    (tid, uid, sid, amount, hashed, 'ACTIVE', now, expires))
        content = f'Giao dịch: {tid}\nNgười nộp: {profile["full_name"]}\nMSSV: {sid}\nSố tiền: {amount:,} VND\nOTP: {code}\nCó hiệu lực {ttl} giây kể từ khi tạo giao dịch. Không chia sẻ mã OTP.'
        queue_email(con, 'otp-' + tid, profile['email'], 'Mã OTP thanh toán học phí', content,
                    {'transaction_id': tid, 'user_id': uid, 'student_id': sid, 'amount': amount})
        con.commit()
    return 201, {'transaction_id': tid, 'status': 'ACTIVE', 'expires_at': expires, 'email_queued': True}


def verify_otp(body):
    tid = transaction_id(body)
    uid = integer_value(body, 'user_id')
    code = text_value(body, 'otp', r'[0-9]{6}', maximum=6)
    now = int(time.time())
    with database('notifications') as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT * FROM otp_challenges WHERE transaction_id=? AND user_id=?', (tid, uid)).fetchone()
        if not row:
            raise ApiError(404, 'OTP_NOT_FOUND', 'Không tìm thấy OTP cho người nộp và giao dịch này.')
        if row['status'] == 'CONSUMED':
            raise ApiError(409, 'OTP_ALREADY_USED', 'OTP đã được xác thực thành công trước đó.')
        if row['status'] == 'LOCKED':
            raise ApiError(429, 'OTP_LOCKED', 'OTP bị khóa do nhập sai 5 lần.')
        if row['status'] == 'EXPIRED' or row['expires_at'] <= now:
            con.execute("UPDATE otp_challenges SET status='EXPIRED' WHERE transaction_id=?", (tid,))
            con.commit()
            raise ApiError(410, 'OTP_EXPIRED', 'OTP đã hết hạn.')
        if not hmac.compare_digest(row['code_hash'], fingerprint(code)):
            attempts = row['attempts'] + 1
            state = 'LOCKED' if attempts >= 5 else 'ACTIVE'
            con.execute('UPDATE otp_challenges SET attempts=?,status=? WHERE transaction_id=?', (attempts, state, tid))
            con.commit()
            raise ApiError(429 if state == 'LOCKED' else 400, 'OTP_LOCKED' if state == 'LOCKED' else 'OTP_INVALID', 'OTP không đúng. Tối đa 5 lần thử.')
        verification_id = secrets.token_urlsafe(24)
        con.execute("UPDATE otp_challenges SET status='CONSUMED',consumed_at=?,verification_id=? WHERE transaction_id=?", (now, verification_id, tid))
        con.commit()
    return 200, {'verified': True, 'verification_id': verification_id, 'transaction_id': tid,
                 'user_id': uid, 'student_id': row['student_id'], 'amount': row['amount'], 'consumed_at': now}


def receipt(body):
    tid = transaction_id(body)
    uid = integer_value(body, 'user_id')
    amount = integer_value(body, 'amount')
    sid = text_value(body, 'student_id', r'[A-Za-z0-9]{3,30}')
    profile = user_contact(uid)
    with database('notifications') as con:
        con.execute('BEGIN IMMEDIATE')
        old = queue_email(con, 'receipt-' + tid, profile['email'], 'Thanh toán học phí thành công',
                          f'Giao dịch: {tid}\nMSSV: {sid}\nĐã thanh toán toàn bộ: {amount:,} VND.\nCảm ơn bạn đã sử dụng iBanking.',
                          {'transaction_id': tid, 'user_id': uid, 'student_id': sid, 'amount': amount})
        con.commit()
    return (200 if old else 202), {'event_id': 'receipt-' + tid, 'email_queued': True}


def send_email(row):
    config = load_mail_config()
    message = EmailMessage()
    message['From'] = config.sender
    message['To'] = row['recipient']
    message['Subject'] = row['subject']
    message['Message-ID'] = '<' + fingerprint(row['event_id']) + '@' + config.sender.split('@')[-1] + '>'
    message.set_content(row['body'])
    factory = smtplib.SMTP_SSL if config.security == 'ssl' else smtplib.SMTP
    kwargs = {'context': ssl.create_default_context()} if config.security == 'ssl' else {}
    with factory(config.host, config.port, timeout=8, **kwargs) as smtp:
        if config.security == 'starttls':
            smtp.starttls(context=ssl.create_default_context())
        if config.username:
            smtp.login(config.username, config.password)
        smtp.send_message(message)


def dispatch_once():
    now = int(time.time())
    with database('notifications') as con:
        con.execute('BEGIN IMMEDIATE')
        con.execute("UPDATE email_outbox SET status='CANCELLED',body='' WHERE status IN ('PENDING','SENDING') AND lease_until<=? AND event_id IN (SELECT 'otp-' || transaction_id FROM otp_challenges WHERE expires_at<=? OR status IN ('CONSUMED','LOCKED','EXPIRED'))", (now, now))
        row = con.execute("SELECT * FROM email_outbox WHERE (status='PENDING' AND next_attempt<=?) OR (status='SENDING' AND lease_until<=?) ORDER BY rowid LIMIT 1", (now, now)).fetchone()
        if not row:
            con.commit()
            return
        con.execute("UPDATE email_outbox SET status='SENDING',lease_until=?,attempts=attempts+1 WHERE event_id=?", (now + 30, row['event_id']))
        con.commit()
    try:
        send_email(row)
    except Exception as error:
        print('SMTP retry:', type(error).__name__, flush=True)
        with database('notifications') as con:
            con.execute("UPDATE email_outbox SET status='PENDING',lease_until=0,next_attempt=? WHERE event_id=?", (int(time.time()) + min(60, 2 ** min(row['attempts'] + 1, 6)), row['event_id']))
    else:
        with database('notifications') as con:
            con.execute("UPDATE email_outbox SET status='SENT',sent_at=?,lease_until=0,body='' WHERE event_id=?", (int(time.time()), row['event_id']))


def dispatch_forever():
    while True:
        try:
            dispatch_once()
        except Exception as error:
            print('Outbox retry:', type(error).__name__, flush=True)
        time.sleep(0.3)


class API(Handler):
    def route(self):
        if self.path == '/health' and self.command == 'GET':
            return 200, {'status': 'ok', 'service': 'notification'}
        require_internal(self.headers)
        if self.path == '/internal/otp' and self.command == 'POST':
            return create_otp(self.body)
        if self.path == '/internal/otp/verify' and self.command == 'POST':
            return verify_otp(self.body)
        if self.path == '/internal/receipts' and self.command == 'POST':
            return receipt(self.body)
        otp = re.fullmatch(r'/internal/otp/([A-Za-z0-9_-]{3,100})/status', self.path)
        outbox = re.fullmatch(r'/internal/outbox/([A-Za-z0-9_-]{3,110})', self.path)
        if self.command == 'GET':
            with database('notifications') as con:
                if otp:
                    row = con.execute('SELECT transaction_id,user_id,student_id,amount,status,expires_at,consumed_at,verification_id FROM otp_challenges WHERE transaction_id=?', (otp[1],)).fetchone()
                    if row:
                        result = dict(row)
                        if result['status'] == 'ACTIVE' and result['expires_at'] <= time.time():
                            result['status'] = 'EXPIRED'
                        return 200, result
                if outbox:
                    row = con.execute('SELECT event_id,status,attempts,sent_at FROM email_outbox WHERE event_id=?', (outbox[1],)).fetchone()
                    if row:
                        return 200, dict(row)
        raise ApiError(404, 'NOT_FOUND', 'Không tìm thấy API hoặc dữ liệu.')


if __name__ == '__main__':
    try:
        load_mail_config()
    except ValueError as error:
        raise SystemExit(str(error))
    initialize('notifications', 'notification_schema.sql')
    threading.Thread(target=dispatch_forever, daemon=True).start()
    serve(API, 8003)
