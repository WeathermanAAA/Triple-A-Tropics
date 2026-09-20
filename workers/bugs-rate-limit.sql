-- Private anti-abuse counters only; no raw addresses or report content.
CREATE TABLE IF NOT EXISTS report_limits (
  id TEXT PRIMARY KEY,
  client_key TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS report_limits_client ON report_limits(client_key);
CREATE INDEX IF NOT EXISTS report_limits_time ON report_limits(created_at);
