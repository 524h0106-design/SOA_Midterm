import os
import re
from dataclasses import dataclass, field

LINH_EMAIL = 'linhkatie0707@gmail.com'
TIEN_EMAIL = '524H0128@student.tdtu.edu.vn'


def valid_email(value):
    return isinstance(value, str) and re.fullmatch(r'[^\s@\r\n]+@[^\s@\r\n]+\.[^\s@\r\n]+', value) is not None


@dataclass(frozen=True)
class MailConfig:
    host: str
    port: int
    security: str
    username: str
    password: str = field(repr=False)
    sender: str = ''


def load_mail_config(environ=None):
    env = os.environ if environ is None else environ
    host = env.get('SMTP_HOST', 'smtp.gmail.com').strip()
    gmail = host.lower() == 'smtp.gmail.com'
    security = env.get('SMTP_SECURITY', 'starttls' if gmail else 'none').strip().lower()
    if not host or security not in ('none', 'starttls', 'ssl'):
        raise ValueError('SMTP_HOST hoặc SMTP_SECURITY không hợp lệ.')
    try:
        port = int(env.get('SMTP_PORT', '587' if gmail else '1025'))
    except (ValueError, TypeError):
        raise ValueError('SMTP_PORT phải là số nguyên.') from None
    if not 1 <= port <= 65535:
        raise ValueError('SMTP_PORT phải nằm trong khoảng 1 đến 65535.')
    username = env.get('SMTP_USER', LINH_EMAIL if gmail else '').strip()
    password = env.get('SMTP_PASSWORD', '')
    if gmail:
        password = ''.join(password.split())
        if security == 'none':
            raise ValueError('Gmail cần STARTTLS hoặc SSL.')
        if not valid_email(username) or len(password) != 16 or password.startswith('REPLACE_'):
            raise ValueError('Chưa cấu hình Gmail hợp lệ. Chạy py configure_email.py và nhập mật khẩu ứng dụng Google gồm 16 ký tự.')
    elif username and not password:
        raise ValueError('Đã khai báo SMTP_USER nhưng chưa có SMTP_PASSWORD.')
    sender = env.get('MAIL_FROM', username or 'ibanking@demo.local').strip()
    if not valid_email(sender):
        raise ValueError('MAIL_FROM phải là địa chỉ email hợp lệ.')
    if gmail and sender.lower() != username.lower():
        raise ValueError('Với cấu hình Gmail này, MAIL_FROM cần trùng SMTP_USER.')
    return MailConfig(host, port, security, username, password, sender)
