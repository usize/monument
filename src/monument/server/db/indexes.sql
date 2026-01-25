-- Monument DB indexes
-- Applied after schema creation

-- Tile history lookups by tick and position
CREATE INDEX IF NOT EXISTS idx_tile_history_tick ON tile_history(supertick_id);
CREATE INDEX IF NOT EXISTS idx_tile_history_pos ON tile_history(x, y);
CREATE INDEX IF NOT EXISTS idx_tile_history_actor ON tile_history(actor_id);

-- Journal lookups by tick
CREATE INDEX IF NOT EXISTS idx_journal_tick ON journal(supertick_id);

-- Audit lookups by tick and actor
CREATE INDEX IF NOT EXISTS idx_audit_tick ON audit(supertick_id);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit(actor_id);

-- Chat lookups by tick
CREATE INDEX IF NOT EXISTS idx_chat_tick ON chat(supertick_id);

-- Scoring rounds by tick
CREATE INDEX IF NOT EXISTS idx_scoring_tick ON scoring_rounds(supertick_id);
