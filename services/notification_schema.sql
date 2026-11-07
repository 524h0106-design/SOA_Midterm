CREATE TABLE IF NOT EXISTS otp_challenges (
    transaction_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    student_id TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK (amount > 0),
    code_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE','CONSUMED','EXPIRED','LOCKED')),
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    consumed_at INTEGER,
    verification_id TEXT UNIQUE
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_active_otp ON otp_challenges(code_hash) WHERE status='ACTIVE';
CREATE TABLE IF NOT EXISTS email_outbox (
    event_id TEXT PRIMARY KEY,
    payload_hash TEXT NOT NULL,
    recipient TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','SENDING','SENT','CANCELLED')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt INTEGER NOT NULL DEFAULT 0,
    lease_until INTEGER NOT NULL DEFAULT 0,
    sent_at INTEGER
);
