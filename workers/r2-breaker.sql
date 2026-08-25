-- D1 schema for the r2-breaker Worker (database tat-breaker, binding DB).
-- Idempotent: safe to re-apply on every deploy (deploy-breaker.sh does).
-- MUST match the SCHEMA array in r2-breaker.js (tests/test_r2_breaker.py
-- pins the two copies to each other).
CREATE TABLE IF NOT EXISTS state (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ticks (ts TEXT PRIMARY KEY, rate_1h INTEGER, pace_15m INTEGER, verdict TEXT, mode TEXT, writes_enabled INTEGER, error TEXT, latency_ms INTEGER);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT, issue_url TEXT);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
