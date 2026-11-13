import importlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'services'))
from configure_email import configure, read_values
from services.mail_config import LINH_EMAIL, TIEN_EMAIL, load_mail_config


class EmailConfigurationTest(unittest.TestCase):
    def gmail(self):
        return {'SMTP_HOST': 'smtp.gmail.com', 'SMTP_PORT': '587', 'SMTP_SECURITY': 'starttls',
                'SMTP_USER': LINH_EMAIL, 'SMTP_PASSWORD': 'abcd efgh ijkl mnop', 'MAIL_FROM': LINH_EMAIL}

    def test_missing_gmail_app_password_fails_clearly(self):
        with self.assertRaisesRegex(ValueError, 'configure_email.py'):
            load_mail_config({})

    def test_launcher_explains_missing_gmail_setup(self):
        with tempfile.TemporaryDirectory() as folder:
            env = {**os.environ, 'DATA_DIR': folder, 'SERVICE_KEY': secrets.token_urlsafe(48),
                   'SMTP_HOST': 'smtp.gmail.com', 'SMTP_PORT': '587', 'SMTP_SECURITY': 'starttls',
                   'SMTP_USER': LINH_EMAIL, 'SMTP_PASSWORD': '', 'MAIL_FROM': LINH_EMAIL}
            result = subprocess.run([sys.executable, 'run.py'], cwd=ROOT, env=env,
                                    capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('configure_email.py', result.stdout + result.stderr)
            self.assertNotIn('Traceback', result.stdout + result.stderr)

    def test_gmail_normalizes_app_password_and_hides_it_in_repr(self):
        config = load_mail_config(self.gmail())
        self.assertEqual(config.password, 'abcdefghijklmnop')
        self.assertEqual(config.security, 'starttls')
        self.assertNotIn(config.password, repr(config))

    def test_gmail_rejects_plaintext_and_mismatched_sender(self):
        for change in [{'SMTP_SECURITY': 'none'}, {'MAIL_FROM': TIEN_EMAIL}, {'SMTP_PASSWORD': ''}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                load_mail_config({**self.gmail(), **change})

    def test_setup_preserves_service_key_and_other_services(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            key = secrets.token_urlsafe(48)
            (root / '.env').write_text(f'SERVICE_KEY={key}\nPAYMENT_URL=http://payment:8002\nSMTP_HOST=mailpit\nSMTP_HOST=localhost\n', encoding='utf-8')
            configure(LINH_EMAIL, 'abcd efgh ijkl mnop', root)
            result = read_values(root / '.env')
            self.assertEqual(result['SERVICE_KEY'], key)
            self.assertEqual(result['PAYMENT_URL'], 'http://payment:8002')
            self.assertEqual(result['SMTP_HOST'], 'smtp.gmail.com')
            self.assertEqual(result['DOCKER_SMTP_HOST'], 'smtp.gmail.com')
            self.assertEqual(result['DEMO_LINH_EMAIL'], LINH_EMAIL)
            self.assertEqual(result['DEMO_SECOND_EMAIL'], TIEN_EMAIL)
            self.assertEqual((root / '.env').read_text().count('SMTP_HOST='), 2)

    def test_setup_reuses_runtime_key(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'data').mkdir()
            key = secrets.token_urlsafe(48)
            (root / 'data/runtime-key.json').write_text(json.dumps({'SERVICE_KEY': key}))
            configure(LINH_EMAIL, 'abcdefghijklmnop', root)
            self.assertEqual(read_values(root / '.env')['SERVICE_KEY'], key)

    def test_invalid_setup_does_not_overwrite_existing_env(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / '.env').write_text('KEEP=unchanged\n')
            with self.assertRaises(ValueError):
                configure(LINH_EMAIL, 'bad', root)
            self.assertEqual((root / '.env').read_text(), 'KEEP=unchanged\n')

    def test_smtp_starttls_authentication_and_both_real_recipients(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {'SERVICE_KEY': secrets.token_urlsafe(48), 'DATA_DIR': folder}):
                notification = importlib.import_module('notification_service')
            with patch.dict(os.environ, self.gmail(), clear=True), patch.object(notification.smtplib, 'SMTP') as smtp_class:
                smtp = smtp_class.return_value.__enter__.return_value
                for number, recipient in enumerate([LINH_EMAIL, TIEN_EMAIL]):
                    notification.send_email({'event_id': f'test-{number}', 'recipient': recipient,
                                             'subject': 'OTP test', 'body': 'Test transport only'})
                self.assertEqual(smtp.starttls.call_count, 2)
                smtp.login.assert_called_with(LINH_EMAIL, 'abcdefghijklmnop')
                sent = [call.args[0] for call in smtp.send_message.call_args_list]
                self.assertEqual([message['To'] for message in sent], [LINH_EMAIL, TIEN_EMAIL])
                self.assertTrue(all(message['From'] == LINH_EMAIL for message in sent))
                self.assertEqual(smtp_class.call_args.args, ('smtp.gmail.com', 587))

    def test_smtp_ssl_transport_and_no_starttls(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {'SERVICE_KEY': secrets.token_urlsafe(48), 'DATA_DIR': folder}):
                notification = importlib.import_module('notification_service')
            with patch.dict(os.environ, {**self.gmail(), 'SMTP_PORT': '465', 'SMTP_SECURITY': 'ssl'}, clear=True), patch.object(notification.smtplib, 'SMTP_SSL') as smtp_class:
                smtp = smtp_class.return_value.__enter__.return_value
                notification.send_email({'event_id': 'test-ssl', 'recipient': TIEN_EMAIL, 'subject': 'OTP test', 'body': 'Test transport only'})
                smtp.starttls.assert_not_called()
                smtp.login.assert_called_once_with(LINH_EMAIL, 'abcdefghijklmnop')
                self.assertIn('context', smtp_class.call_args.kwargs)

    def test_existing_accounts_get_real_email_without_reset(self):
        script = '''
import json
from common import database
from user_service import initialize_users
initialize_users()
with database('users') as c:
    c.execute("UPDATE users SET balance=123456,email='linh@example.com' WHERE id=1")
    c.execute("UPDATE users SET email='minh@example.com',full_name='Tài khoản kiểm thử thứ hai' WHERE id=2")
    c.execute("INSERT INTO sessions VALUES('existing-session',1,2000000000)")
    before=dict(c.execute('SELECT * FROM users WHERE id=1').fetchone())
initialize_users()
with database('users') as c:
    after=dict(c.execute('SELECT * FROM users WHERE id=1').fetchone())
    assert after['email']=='linhkatie0707@gmail.com'
    assert all(after[k]==v for k,v in before.items() if k!='email')
    second=dict(c.execute('SELECT * FROM users WHERE id=2').fetchone())
    assert second['email']=='524H0128@student.tdtu.edu.vn'
    assert second['username']=='minh' and second['full_name']=='Nguyễn Tiến'
    assert c.execute("SELECT 1 FROM sessions WHERE token_hash='existing-session'").fetchone()
print('migration passed')
'''
        with tempfile.TemporaryDirectory() as folder:
            env = {key: value for key, value in os.environ.items() if key not in ('DEMO_LINH_EMAIL', 'DEMO_SECOND_EMAIL')}
            result = subprocess.run([sys.executable, '-c', script], cwd=ROOT / 'services',
                env={**env, 'SERVICE_KEY': secrets.token_urlsafe(48), 'DATA_DIR': folder}, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('migration passed', result.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
