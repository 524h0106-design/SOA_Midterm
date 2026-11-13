import mimetypes
import os
import re
from urllib.parse import urlsplit
from common import ApiError, Handler, ROOT, remote, serve

USER_URL = os.environ.get('USER_URL', 'http://127.0.0.1:8001')
PAYMENT_URL = os.environ.get('PAYMENT_URL', 'http://127.0.0.1:8002')


class Gateway(Handler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path.startswith('/api/'):
            return self.handle_request()
        filenames = {'/': 'index.html', '/index.html': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}
        if path not in filenames:
            return self.send_json(404, {'error': {'code': 'NOT_FOUND', 'message': 'Không tìm thấy trang.'}})
        filename = filenames[path]
        content = (ROOT / 'web' / filename).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', (mimetypes.guess_type(filename)[0] or 'text/plain') + '; charset=utf-8')
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'")
        self.end_headers()
        self.wfile.write(content)

    def route(self):
        headers = {key: self.headers[key] for key in ('Authorization', 'Idempotency-Key') if self.headers.get(key)}
        path = self.path.removeprefix('/api')
        allowed = {('/auth/login', 'POST'), ('/auth/logout', 'POST'), ('/auth/me', 'GET'), ('/account', 'GET'), ('/transactions', 'GET')}
        if (path, self.command) in allowed:
            return remote(USER_URL, path, self.command, self.body if self.command == 'POST' else None, headers)
        payment_route = (self.command == 'GET' and re.fullmatch(r'/(tuition(?:/[A-Za-z0-9]{3,30})?|payments/[A-Za-z0-9_-]{3,100})', path)) or (self.command == 'POST' and (path == '/payments' or re.fullmatch(r'/payments/[A-Za-z0-9_-]{3,100}/confirm', path)))
        if payment_route:
            status, profile = remote(USER_URL, '/auth/me', headers=headers)
            if status != 200:
                return status, profile
            return remote(PAYMENT_URL, path, self.command, self.body if self.command == 'POST' else None, headers)
        raise ApiError(404, 'NOT_FOUND', 'Không tìm thấy API công khai.')


if __name__ == '__main__':
    serve(Gateway, 8080)
