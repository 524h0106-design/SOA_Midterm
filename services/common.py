import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('DATA_DIR', str(ROOT / 'data')))
DATA.mkdir(parents=True, exist_ok=True)
SERVICE_KEY = os.environ.get('SERVICE_KEY', '')
if len(SERVICE_KEY) < 32 or SERVICE_KEY.startswith('REPLACE_'):
    raise RuntimeError('SERVICE_KEY phải có ít nhất 32 ký tự. Chạy python run.py hoặc cấu hình .env.')


class ApiError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


def connect(name):
    con = sqlite3.connect(DATA / (name + '.db'), timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    con.execute('PRAGMA busy_timeout=15000')
    return con


def database(name):
    return closing(connect(name))


def initialize(name, filename):
    with database(name) as con:
        con.execute('PRAGMA journal_mode=WAL')
        con.executescript((ROOT / 'services' / filename).read_text(encoding='utf-8'))


def fingerprint(value):
    return hmac.new(SERVICE_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()


def require_internal(headers):
    if not hmac.compare_digest(headers.get('X-Service-Key', ''), SERVICE_KEY):
        raise ApiError(403, 'FORBIDDEN', 'Chỉ dịch vụ nội bộ được truy cập.')


def text_value(body, key, pattern=None, maximum=100):
    value = body.get(key)
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise ApiError(400, 'INVALID_INPUT', f'{key} không hợp lệ.')
    if pattern and not re.fullmatch(pattern, value):
        raise ApiError(400, 'INVALID_INPUT', f'{key} không hợp lệ.')
    return value


def integer_value(body, key, minimum=1, maximum=1_000_000_000_000):
    value = body.get(key)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ApiError(400, 'INVALID_INPUT', f'{key} phải là số nguyên hợp lệ.')
    return value


def transaction_id(body):
    return text_value(body, 'transaction_id', r'[A-Za-z0-9_-]{3,100}')


def remote(base, path, method='GET', body=None, headers=None):
    payload = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(base.rstrip('/') + path, data=payload, method=method,
        headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.load(error)
        except (ValueError, UnicodeError):
            raise ApiError(502, 'INVALID_UPSTREAM', 'Dịch vụ liên kết trả dữ liệu không hợp lệ.')
    except (OSError, ValueError, UnicodeError):
        raise ApiError(503, 'SERVICE_UNAVAILABLE', 'Dịch vụ liên kết chưa chạy hoặc chưa tích hợp.')


class Handler(BaseHTTPRequestHandler):
    server_version = 'iBanking'

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def send_json(self, status, value):
        payload = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(payload)

    def handle_request(self):
        try:
            self.path = urlsplit(self.path).path
            self.body = {}
            if self.command == 'POST':
                if not self.headers.get('Content-Type', '').startswith('application/json'):
                    raise ApiError(415, 'JSON_REQUIRED', 'Yêu cầu phải có Content-Type application/json.')
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 16384:
                    raise ApiError(400, 'INVALID_BODY', 'Nội dung yêu cầu không hợp lệ.')
                self.body = json.loads(self.rfile.read(length))
                if not isinstance(self.body, dict):
                    raise ApiError(400, 'INVALID_BODY', 'Nội dung yêu cầu phải là JSON object.')
            status, result = self.route()
        except ApiError as error:
            status, result = error.status, {'error': {'code': error.code, 'message': error.message}}
        except (ValueError, TypeError, KeyError, UnicodeError):
            status, result = 400, {'error': {'code': 'INVALID_INPUT', 'message': 'Dữ liệu không hợp lệ.'}}
        except sqlite3.OperationalError:
            status, result = 503, {'error': {'code': 'DATABASE_BUSY', 'message': 'Dữ liệu đang bận, vui lòng thử lại.'}}
        except Exception as error:
            print('Request error:', type(error).__name__, flush=True)
            status, result = 500, {'error': {'code': 'INTERNAL_ERROR', 'message': 'Không thể xử lý yêu cầu.'}}
        self.send_json(status, result)


def serve(handler, default_port):
    server = ThreadingHTTPServer((os.environ.get('BIND_HOST', '127.0.0.1'), int(os.environ.get('PORT', default_port))), handler)
    server.daemon_threads = True
    server.serve_forever()
