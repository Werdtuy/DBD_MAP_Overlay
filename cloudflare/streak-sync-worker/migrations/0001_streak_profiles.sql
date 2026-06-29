CREATE TABLE IF NOT EXISTS streak_profiles (
  tag TEXT PRIMARY KEY,
  tag_normalized TEXT NOT NULL UNIQUE,
  player_id TEXT NOT NULL,
  state_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_streak_profiles_updated_at
ON streak_profiles(updated_at);

CREATE TABLE IF NOT EXISTS streak_lobbies (
  code TEXT PRIMARY KEY,
  state_json TEXT NOT NULL,
  members_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_streak_lobbies_updated_at
ON streak_lobbies(updated_at);

CREATE INDEX IF NOT EXISTS idx_streak_lobbies_expires_at
ON streak_lobbies(expires_at);
