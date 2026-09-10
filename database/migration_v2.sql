-- =====================================================================
-- Migration v2 — room lifecycle (WAITING/ACTIVE/COMPLETED/EXPIRED/CLOSED),
-- assessment window (participant limit + from/to date), and attempt
-- status (COMPLETED vs TIME EXPIRED), matching the RAMCO reference app.
--
-- Run this ONCE against the existing EmergIQ-platform MySQL database,
-- after taking a backup. If you already partially ran an earlier version
-- of this file and hit "Data truncated for column 'status'", use
-- migration_v2_resume.sql instead — it picks up from exactly that point.
-- =====================================================================

ALTER TABLE rooms
    ADD COLUMN participant_limit INTEGER NOT NULL DEFAULT 50 AFTER difficulty;

ALTER TABLE rooms
    ADD COLUMN from_date DATE NULL AFTER level_set;

ALTER TABLE rooms
    ADD COLUMN to_date DATE NULL AFTER from_date;

ALTER TABLE rooms
    ADD COLUMN started_at DATETIME NULL AFTER created_at;

ALTER TABLE rooms
    ADD COLUMN completed_at DATETIME NULL AFTER started_at;

-- Widen the status enum in two steps: MySQL refuses to convert existing
-- 'open'/'closed' rows straight into an enum that doesn't list 'open' as a
-- valid value ("Data truncated for column 'status'"), so first widen it to
-- include both the old and new values...
ALTER TABLE rooms
    MODIFY COLUMN status ENUM('open','closed','waiting','active','completed','expired') NOT NULL DEFAULT 'waiting';

-- ...then map old values onto the new lifecycle. 'open' meant
-- joinable/playable, which is closest to 'active'.
UPDATE rooms SET status = 'active' WHERE status = 'open';

-- ...then drop 'open' from the enum now that nothing uses it.
ALTER TABLE rooms
    MODIFY COLUMN status ENUM('waiting','active','completed','expired','closed') NOT NULL DEFAULT 'waiting';

-- Existing rooms have no assessment window — default to a 7-day window
-- starting the day they were created, so they don't get force-expired
-- the moment this migration runs.
UPDATE rooms
   SET from_date = DATE(created_at),
       to_date   = DATE_ADD(DATE(created_at), INTERVAL 7 DAY)
 WHERE from_date IS NULL;

ALTER TABLE attempts
    ADD COLUMN status ENUM('completed','time_expired') NULL AFTER completed_at;

-- Every attempt that already has a completed_at timestamp finished
-- normally (server-side timeout enforcement did not exist before this
-- migration), so backfill those as 'completed'.
UPDATE attempts SET status = 'completed' WHERE completed_at IS NOT NULL AND status IS NULL;
