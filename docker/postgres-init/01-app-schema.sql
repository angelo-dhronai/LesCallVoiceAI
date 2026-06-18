
CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    email       TEXT    NOT NULL UNIQUE,
    name        TEXT,
    google_sub  TEXT    UNIQUE,
    picture     TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login  TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agents (
    id              SERIAL PRIMARY KEY,
    owner           TEXT,
    name            TEXT    NOT NULL,
    company         TEXT,
    persona         TEXT,
    system_prompt   TEXT    NOT NULL,
    pitch           TEXT,
    language        TEXT    DEFAULT 'auto',
    tts_speaker     TEXT    DEFAULT 'shubh',
    greeting        TEXT,
    knowledge       TEXT,
    stages          TEXT,
    is_active       INTEGER DEFAULT 1,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS campaigns (
    id              SERIAL PRIMARY KEY,
    owner           TEXT,
    name            TEXT    NOT NULL,
    agent_id        INTEGER REFERENCES agents(id) ON DELETE SET NULL,
    caller_id       TEXT,
    gateway         TEXT,
    status          TEXT    DEFAULT 'draft',
    max_concurrency INTEGER DEFAULT 1,
    retry_limit     INTEGER DEFAULT 2,
    schedule        TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS leads (
    id              SERIAL PRIMARY KEY,
    campaign_id     INTEGER REFERENCES campaigns(id) ON DELETE CASCADE,
    name            TEXT,
    phone           TEXT    NOT NULL,
    status          TEXT    DEFAULT 'pending',
    disposition     TEXT,
    attempts        INTEGER DEFAULT 0,
    last_called_at  TIMESTAMP,
    notes           TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_leads_campaign ON leads(campaign_id, status);

CREATE TABLE IF NOT EXISTS calls (
    id              SERIAL PRIMARY KEY,
    conn_id         TEXT    UNIQUE,
    direction       TEXT    NOT NULL,
    agent_id        INTEGER REFERENCES agents(id) ON DELETE SET NULL,
    campaign_id     INTEGER REFERENCES campaigns(id) ON DELETE SET NULL,
    lead_id         INTEGER REFERENCES leads(id) ON DELETE SET NULL,
    phone           TEXT,
    language        TEXT,
    started_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at        TIMESTAMP,
    duration_s      REAL,
    recording_path  TEXT,
    transcript      TEXT,
    summary         TEXT,
    sentiment       TEXT,
    outcome         TEXT,
    cost            REAL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_calls_direction ON calls(direction, started_at);
CREATE INDEX IF NOT EXISTS idx_calls_campaign  ON calls(campaign_id);

CREATE TABLE IF NOT EXISTS trunks (
    id              SERIAL PRIMARY KEY,
    name            TEXT    NOT NULL UNIQUE,
    provider        TEXT    DEFAULT 'plivo',
    owner           TEXT,
    sip_domain      TEXT    NOT NULL,
    proxy           TEXT,
    username        TEXT,
    password        TEXT,
    register        INTEGER DEFAULT 0,
    caller_id       TEXT,
    provider_ips    TEXT,                 -- comma-separated CIDRs to allow inbound (ACL)
    transport       TEXT    DEFAULT 'udp',
    config_status   TEXT    DEFAULT 'pending',
    last_applied_at TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS phone_numbers (
    id               SERIAL PRIMARY KEY,
    number           TEXT    NOT NULL UNIQUE,
    trunk_id         INTEGER REFERENCES trunks(id) ON DELETE SET NULL,
    inbound_agent_id INTEGER REFERENCES agents(id) ON DELETE SET NULL,
    owner            TEXT,
    direction        TEXT    DEFAULT 'both',
    label            TEXT,
    config_status    TEXT    DEFAULT 'pending',
    last_applied_at  TIMESTAMP,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_numbers_trunk ON phone_numbers(trunk_id);