-- lescall data model
-- DB-backed agent definitions, outbound campaigns, leads, and unified call records.
-- Shared by the dashboard API, the outbound dialer engine, and (at call time) the agent core.
-- Engine: SQLite (file at data/lescall.db). Schema is Postgres-portable.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- agents: a configurable AI persona. Replaces the hardcoded Vishal Patel / Pramukh
-- definition. Loaded at call time by the agent core (inbound or outbound).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,                 -- agent display name e.g. "Vishal Patel"
    company         TEXT,                             -- e.g. "Pramukh Group"
    persona         TEXT,                             -- short character description
    system_prompt   TEXT    NOT NULL,                 -- full LLM system prompt template
    pitch           TEXT,                             -- the sales pitch (pitch maker output)
    language        TEXT    DEFAULT 'auto',           -- auto | en-IN | hi-IN | gu-IN
    tts_speaker     TEXT    DEFAULT 'shubh',          -- Sarvam bulbul speaker
    greeting        TEXT,                             -- opening line (inbound)
    knowledge       TEXT,                             -- JSON: projects, locations, FAQs
    stages          TEXT,                             -- JSON: ordered stage machine config
    is_active       INTEGER DEFAULT 1,
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- campaigns: an outbound dialing campaign bound to one agent.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS campaigns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    agent_id        INTEGER REFERENCES agents(id) ON DELETE SET NULL,
    caller_id       TEXT,                             -- outbound caller ID
    gateway         TEXT,                             -- FreeSWITCH gateway / trunk
    status          TEXT    DEFAULT 'draft',          -- draft | running | paused | done
    max_concurrency INTEGER DEFAULT 1,                -- simultaneous outbound legs
    retry_limit     INTEGER DEFAULT 2,
    schedule        TEXT,                             -- JSON: calling-window rules
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- leads: a contact to be dialed within a campaign.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS leads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id     INTEGER REFERENCES campaigns(id) ON DELETE CASCADE,
    name            TEXT,
    phone           TEXT    NOT NULL,
    status          TEXT    DEFAULT 'pending',        -- pending | dialing | called | failed | dnc
    disposition     TEXT,                             -- interested | not_interested | callback | no_answer | ...
    attempts        INTEGER DEFAULT 0,
    last_called_at  TEXT,
    notes           TEXT,
    created_at      TEXT    DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_leads_campaign ON leads(campaign_id, status);

-- ---------------------------------------------------------------------------
-- calls: unified record for every call, inbound or outbound.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS calls (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conn_id         TEXT    UNIQUE,                   -- ws_audio_receiver connection / FS uuid
    direction       TEXT    NOT NULL,                 -- inbound | outbound
    agent_id        INTEGER REFERENCES agents(id) ON DELETE SET NULL,
    campaign_id     INTEGER REFERENCES campaigns(id) ON DELETE SET NULL,
    lead_id         INTEGER REFERENCES leads(id) ON DELETE SET NULL,
    phone           TEXT,
    language        TEXT,
    started_at      TEXT    DEFAULT (datetime('now')),
    ended_at        TEXT,
    duration_s      REAL,
    recording_path  TEXT,                             -- relative path under recordings/
    transcript      TEXT,                             -- JSON array of {role, text, ts}
    summary         TEXT,                             -- AI-generated summary
    sentiment       TEXT,                             -- positive | neutral | negative
    outcome         TEXT,                             -- booked | interested | rejected | ...
    cost            REAL,                             -- Sarvam/LLM credits for this call
    created_at      TEXT    DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_calls_direction ON calls(direction, started_at);
CREATE INDEX IF NOT EXISTS idx_calls_campaign  ON calls(campaign_id);
