-- 002_gm_blocks: GM-only profile blocks (build plan P2.7, decision U7).
--
-- The GM profile is the player schema plus optional GM-only blocks (truth,
-- secrets, unlocks, table_rules, style, ...). Adding a block is data, not
-- code: the blocks live in one JSONB object keyed by block name, and
-- app/table/gm_context.py renders whatever is present in config order.
-- NULL for every player baseline. Players' briefs never read this column.
--
-- Never edit 001_init.sql (applied-checksum rule, D-25); later changes are new files.

ALTER TABLE character_baselines ADD COLUMN IF NOT EXISTS gm_blocks JSONB;

COMMENT ON COLUMN character_baselines.gm_blocks IS
    'GM-only profile blocks {name: content}; NULL for players (P2.7, U7)';
