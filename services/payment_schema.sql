CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT UNIQUE,
    user_id INTEGER NOT NULL,
    payer_name TEXT NOT NULL,
    payer_phone TEXT NOT NULL,
    payer_email TEXT NOT NULL,
    student_id TEXT NOT NULL,
    student_name TEXT NOT NULL,
    semester TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK (amount > 0),
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'PROCESSING', 'SUCCESS', 'FAILED', 'EXPIRED', 'CANCELLED', 'REFUNDED')),
    failure_code TEXT,
    failure_message TEXT,
    verification_id TEXT,
    debit_completed INTEGER NOT NULL DEFAULT 0,
    tuition_completed INTEGER NOT NULL DEFAULT 0,
    receipt_queued INTEGER NOT NULL DEFAULT 0,
    needs_refund INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    completed_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_payments_user_created ON payments(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_payments_student ON payments(student_id);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
