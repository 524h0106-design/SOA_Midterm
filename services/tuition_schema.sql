CREATE TABLE IF NOT EXISTS students (
    student_id TEXT PRIMARY KEY,
    full_name TEXT NOT NULL,
    faculty TEXT NOT NULL,
    major TEXT NOT NULL,
    academic_year TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tuitions (
    student_id TEXT PRIMARY KEY REFERENCES students(student_id),
    semester TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK (amount > 0),
    status TEXT NOT NULL DEFAULT 'UNPAID' CHECK (status IN ('UNPAID', 'RESERVED', 'PAID')),
    reserved_by TEXT,
    reserved_until INTEGER NOT NULL DEFAULT 0,
    paid_transaction_id TEXT UNIQUE,
    paid_by_user_id INTEGER,
    paid_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_tuitions_status ON tuitions(status);
