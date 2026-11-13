import re
import time
from common import ApiError, Handler, database, initialize, integer_value, require_internal, serve, text_value, transaction_id

RESERVATION_TTL = 30

SEED_DATA = [
    ('524H0101', 'Nguyễn Hoàng Phúc', 'Công nghệ thông tin', 'Kỹ thuật phần mềm', '2024-2028', 'HK1 (2026-2027)', 8500000, 'UNPAID', None, None, None),
    ('524H0102', 'Lê Thị Bảo Ngọc', 'Quản trị kinh doanh', 'Marketing', '2024-2028', 'HK1 (2026-2027)', 9200000, 'UNPAID', None, None, None),
    ('524H0103', 'Trần Quốc Bảo', 'Điện - Điện tử', 'Kỹ thuật điện tử', '2024-2028', 'HK1 (2026-2027)', 10500000, 'UNPAID', None, None, None),
    ('524H0104', 'Võ Minh Khôi', 'Kế toán', 'Kế toán doanh nghiệp', '2024-2028', 'HK1 (2026-2027)', 11000000, 'UNPAID', None, None, None),
    ('524H0105', 'Đặng Ngọc Hân', 'Mỹ thuật công nghiệp', 'Thiết kế đồ họa', '2024-2028', 'HK1 (2026-2027)', 13500000, 'PAID', 'seed-paid-524H0105', 2, 1770000000),
    ('524H0106', 'Phan Trường Nhất Linh', 'Công nghệ thông tin', 'Kỹ thuật phần mềm', '2024-2028', 'HK1 (2026-2027)', 18500000, 'UNPAID', None, None, None),
    ('524H0107', 'Nguyễn Văn Minh', 'Công nghệ thông tin', 'Khoa học máy tính', '2024-2028', 'HK1 (2026-2027)', 15000000, 'UNPAID', None, None, None),
    ('524H0108', 'Trần Thị Mai Anh', 'Quản trị kinh doanh', 'Tài chính ngân hàng', '2024-2028', 'HK1 (2026-2027)', 12000000, 'UNPAID', None, None, None),
    ('524H0109', 'Lê Hoàng Nam', 'Điện - Điện tử', 'Kỹ thuật điều khiển', '2024-2028', 'HK1 (2026-2027)', 35000000, 'UNPAID', None, None, None),
    ('524H0110', 'Phạm Minh Tuấn', 'Ngoại ngữ', 'Ngôn ngữ Anh', '2024-2028', 'HK1 (2026-2027)', 14000000, 'PAID', 'seed-paid-524H0110', 1, 1770000000),
]


def initialize_tuitions():
    initialize('tuitions', 'tuition_schema.sql')
    with database('tuitions') as con:
        for sid, name, faculty, major, year, semester, amount, status, paid_tid, paid_uid, paid_at in SEED_DATA:
            con.execute('INSERT OR IGNORE INTO students VALUES(?,?,?,?,?)', (sid, name, faculty, major, year))
            con.execute(
                'INSERT OR IGNORE INTO tuitions(student_id,semester,amount,status,reserved_by,reserved_until,paid_transaction_id,paid_by_user_id,paid_at) VALUES(?,?,?,?,NULL,0,?,?,?)',
                (sid, semester, amount, status, paid_tid, paid_uid, paid_at)
            )


def release_expired(con, now):
    con.execute("UPDATE tuitions SET status='UNPAID',reserved_by=NULL,reserved_until=0 WHERE status='RESERVED' AND reserved_until<=?", (now,))


def fetch_tuition_row(con, sid):
    return con.execute(
        '''SELECT s.student_id, s.full_name AS student_name, s.faculty, s.major, s.academic_year,
                  t.semester, t.amount, t.status, t.reserved_by, t.reserved_until,
                  t.paid_transaction_id, t.paid_by_user_id, t.paid_at
           FROM students s JOIN tuitions t ON t.student_id = s.student_id
           WHERE UPPER(s.student_id) = UPPER(?)''',
        (sid,)
    ).fetchone()


def format_tuition(row):
    data = dict(row)
    return {
        'student_id': data['student_id'],
        'student_name': data['student_name'],
        'faculty': data['faculty'],
        'major': data['major'],
        'academic_year': data['academic_year'],
        'semester': data['semester'],
        'amount': data['amount'],
        'status': data['status'],
        'reserved_by': data['reserved_by'],
        'paid_transaction_id': data['paid_transaction_id'],
        'paid_by_user_id': data['paid_by_user_id'],
        'paid_at': data['paid_at'],
    }


def list_tuitions():
    now = int(time.time())
    with database('tuitions') as con:
        con.execute('BEGIN IMMEDIATE')
        release_expired(con, now)
        rows = con.execute(
            '''SELECT s.student_id, s.full_name AS student_name, s.faculty, s.major, s.academic_year,
                      t.semester, t.amount, t.status, t.reserved_by, t.reserved_until,
                      t.paid_transaction_id, t.paid_by_user_id, t.paid_at
               FROM students s JOIN tuitions t ON t.student_id = s.student_id
               ORDER BY s.student_id ASC'''
        ).fetchall()
        con.commit()
    return 200, {'items': [format_tuition(row) for row in rows]}


def get_tuition(sid):
    now = int(time.time())
    with database('tuitions') as con:
        con.execute('BEGIN IMMEDIATE')
        release_expired(con, now)
        row = fetch_tuition_row(con, sid)
        con.commit()
    if not row:
        raise ApiError(404, 'TUITION_NOT_FOUND', 'Không tìm thấy sinh viên hoặc khoản học phí với MSSV này.')
    return 200, format_tuition(row)


def reserve_tuition(sid, body):
    tid = transaction_id(body)
    uid = integer_value(body, 'user_id')
    amount = integer_value(body, 'amount')
    now = int(time.time())
    with database('tuitions') as con:
        con.execute('BEGIN IMMEDIATE')
        release_expired(con, now)
        row = fetch_tuition_row(con, sid)
        if not row:
            con.rollback()
            raise ApiError(404, 'TUITION_NOT_FOUND', 'Không tìm thấy sinh viên hoặc khoản học phí.')
        actual_sid = row['student_id']
        if row['amount'] != amount:
            con.rollback()
            raise ApiError(409, 'AMOUNT_MISMATCH', 'Số tiền thanh toán không khớp với toàn bộ khoản học phí.')
        if row['status'] == 'PAID':
            con.commit()
            if row['paid_transaction_id'] == tid:
                return 200, format_tuition(row)
            raise ApiError(409, 'TUITION_ALREADY_PAID', 'Khoản học phí này đã được thanh toán.')
        if row['status'] == 'RESERVED' and row['reserved_by'] != tid:
            con.commit()
            raise ApiError(409, 'TUITION_LOCKED', 'Khoản học phí đang được xử lý bởi giao dịch khác.')
        con.execute(
            "UPDATE tuitions SET status='RESERVED',reserved_by=?,reserved_until=? WHERE student_id=?",
            (tid, now + RESERVATION_TTL, actual_sid)
        )
        updated = fetch_tuition_row(con, actual_sid)
        con.commit()
    return 200, format_tuition(updated)


def pay_tuition(sid, body):
    tid = transaction_id(body)
    uid = integer_value(body, 'user_id')
    amount = integer_value(body, 'amount')
    now = int(time.time())
    with database('tuitions') as con:
        con.execute('BEGIN IMMEDIATE')
        release_expired(con, now)
        row = fetch_tuition_row(con, sid)
        if not row:
            con.rollback()
            raise ApiError(404, 'TUITION_NOT_FOUND', 'Không tìm thấy sinh viên hoặc khoản học phí.')
        actual_sid = row['student_id']
        if row['amount'] != amount:
            con.rollback()
            raise ApiError(409, 'AMOUNT_MISMATCH', 'Hệ thống chỉ cho phép thanh toán toàn bộ khoản học phí.')
        if row['status'] == 'PAID':
            con.commit()
            if row['paid_transaction_id'] == tid:
                return 200, format_tuition(row)
            raise ApiError(409, 'TUITION_ALREADY_PAID', 'Khoản học phí này đã được thanh toán.')
        if row['status'] == 'RESERVED' and row['reserved_by'] != tid:
            con.commit()
            raise ApiError(409, 'TUITION_ALREADY_PAID', 'Khoản học phí đang được thanh toán bởi một giao dịch khác.')
        changed = con.execute(
            "UPDATE tuitions SET status='PAID',reserved_by=NULL,reserved_until=0,paid_transaction_id=?,paid_by_user_id=?,paid_at=? WHERE student_id=? AND status IN ('UNPAID','RESERVED')",
            (tid, uid, now, actual_sid)
        ).rowcount
        if not changed:
            con.rollback()
            raise ApiError(409, 'TUITION_ALREADY_PAID', 'Khoản học phí này đã được thanh toán.')
        updated = fetch_tuition_row(con, actual_sid)
        con.commit()
    return (200 if row['status'] == 'PAID' else 201), format_tuition(updated)


def release_tuition(sid, body):
    tid = transaction_id(body)
    now = int(time.time())
    with database('tuitions') as con:
        con.execute('BEGIN IMMEDIATE')
        release_expired(con, now)
        row = fetch_tuition_row(con, sid)
        if not row:
            con.rollback()
            raise ApiError(404, 'TUITION_NOT_FOUND', 'Không tìm thấy khoản học phí.')
        if row['status'] == 'RESERVED' and row['reserved_by'] == tid:
            con.execute("UPDATE tuitions SET status='UNPAID',reserved_by=NULL,reserved_until=0 WHERE student_id=?", (row['student_id'],))
        updated = fetch_tuition_row(con, row['student_id'])
        con.commit()
    return 200, format_tuition(updated)


class API(Handler):
    def route(self):
        if self.path == '/health' and self.command == 'GET':
            return 200, {'status': 'ok', 'service': 'tuition'}
        if self.path in ('/tuition', '/internal/tuition') and self.command == 'GET':
            if self.path.startswith('/internal/'):
                require_internal(self.headers)
            return list_tuitions()
        lookup = re.fullmatch(r'/(?:internal/)?tuition/([A-Za-z0-9]{3,30})', self.path)
        if lookup and self.command == 'GET':
            if self.path.startswith('/internal/'):
                require_internal(self.headers)
            return get_tuition(lookup[1])
        if self.path.startswith('/internal/'):
            require_internal(self.headers)
            reserve = re.fullmatch(r'/internal/tuition/([A-Za-z0-9]{3,30})/reserve', self.path)
            pay = re.fullmatch(r'/internal/tuition/([A-Za-z0-9]{3,30})/pay', self.path)
            release = re.fullmatch(r'/internal/tuition/([A-Za-z0-9]{3,30})/release', self.path)
            if reserve and self.command == 'POST':
                return reserve_tuition(reserve[1], self.body)
            if pay and self.command == 'POST':
                return pay_tuition(pay[1], self.body)
            if release and self.command == 'POST':
                return release_tuition(release[1], self.body)
        raise ApiError(404, 'NOT_FOUND', 'Không tìm thấy API học phí.')


if __name__ == '__main__':
    initialize_tuitions()
    serve(API, 8004)
