-- =====================================================================
-- MT-08 Emergency Response Simulation Platform — database schema
-- Supports 3 report surfaces:
--   1. Per-attempt (per-level) report, generated when a level finishes
--   2. Dashboard report: aggregate across ALL of a learner's attempts
--   3. Assessment/room report: aggregate across all levels played
--      inside ONE room (instructor-created session), shown at room end
-- =====================================================================

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTO_INCREMENT,
    name          VARCHAR(120)        NOT NULL,
    email         VARCHAR(255)        NOT NULL UNIQUE,
    password_hash VARCHAR(255)        NOT NULL,
    role          ENUM('learner', 'instructor') NOT NULL DEFAULT 'learner',
    created_at    DATETIME            NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- A host-created assessment session (RAMCO-style lifecycle). Learners join
-- with `code`. WAITING -> ACTIVE (host starts it) -> COMPLETED (host ends
-- it, or every participant finishes), or EXPIRED once `to_date` passes, or
-- CLOSED (host closes it before it ever started).
CREATE TABLE IF NOT EXISTS rooms (
    id                INTEGER PRIMARY KEY AUTO_INCREMENT,
    code              VARCHAR(12)         NOT NULL UNIQUE,
    name              VARCHAR(150)        NOT NULL,
    instructor_id     INTEGER             NOT NULL,
    difficulty        VARCHAR(20)         DEFAULT 'standard',
    participant_limit INTEGER             NOT NULL DEFAULT 50,
    -- comma/JSON list of level numbers for this room — currently always a
    -- single level (the create-room form has one "Select level" field,
    -- matching RAMCO), but stored as a list for backward compatibility.
    level_set         TEXT                NOT NULL,
    from_date         DATE                NULL,
    to_date           DATE                NULL,
    status            ENUM('waiting', 'active', 'completed', 'expired', 'closed') NOT NULL DEFAULT 'waiting',
    created_at        DATETIME            NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at        DATETIME            NULL,
    completed_at      DATETIME            NULL,
    closed_at         DATETIME            NULL,
    FOREIGN KEY (instructor_id) REFERENCES users (id)
);

-- A learner's membership/participation in a room.
CREATE TABLE IF NOT EXISTS room_members (
    id            INTEGER PRIMARY KEY AUTO_INCREMENT,
    room_id       INTEGER             NOT NULL,
    user_id       INTEGER             NOT NULL,
    joined_at     DATETIME            NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- set when the learner has finished every level in the room's level_set
    -- (or the instructor ends the room) — this is what triggers report #3
    finished_at   DATETIME            NULL,
    UNIQUE KEY uq_room_member (room_id, user_id),
    FOREIGN KEY (room_id) REFERENCES rooms (id),
    FOREIGN KEY (user_id) REFERENCES users (id)
);

-- One playthrough of one level by one learner.
-- room_id is NULL for solo/self-learning play; set when played as part of
-- a room/assessment.
CREATE TABLE IF NOT EXISTS attempts (
    id              INTEGER PRIMARY KEY AUTO_INCREMENT,
    user_id         INTEGER           NOT NULL,
    room_id         INTEGER           NULL,
    level           INTEGER           NOT NULL,
    scenario_title  VARCHAR(200)      NULL,
    started_at      DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at    DATETIME          NULL,
    -- how this attempt ended: the server enforces a real deadline
    -- (see app/game/routes.py) — 'time_expired' when that deadline passed
    -- before the learner finished, 'completed' otherwise.
    status          ENUM('completed', 'time_expired') NULL,
    -- denormalized outcome (metric #5 aggregate for THIS attempt), written
    -- once by report_generator.build_report() so dashboard/room aggregation
    -- never has to recompute from decision_scores every time.
    average_total          DECIMAL(5, 2) NULL,
    clinical_accuracy_avg  DECIMAL(5, 2) NULL,
    response_time_avg      DECIMAL(5, 2) NULL,
    response_efficiency_avg DECIMAL(5, 2) NULL,
    ethical_reasoning_avg  DECIMAL(5, 2) NULL,
    outcome_label   VARCHAR(60)       NULL,
    FOREIGN KEY (user_id) REFERENCES users (id),
    FOREIGN KEY (room_id) REFERENCES rooms (id)
);

CREATE INDEX idx_attempts_user ON attempts (user_id);
CREATE INDEX idx_attempts_room ON attempts (room_id);

-- One evaluated decision point inside one attempt — the row-level detail
-- behind the per-attempt "decision-by-decision breakdown" table.
CREATE TABLE IF NOT EXISTS decision_scores (
    id                       INTEGER PRIMARY KEY AUTO_INCREMENT,
    attempt_id               INTEGER      NOT NULL,
    decision_point_id        VARCHAR(40)  NULL,   -- e.g. "dp1" from scenario JSON
    sequence                 INTEGER      NOT NULL, -- 1-based order within the attempt
    decision_text             TEXT         NULL,
    clinical_verdict          VARCHAR(20)  NOT NULL, -- correct/acceptable/incorrect/harmful
    reasoning                 TEXT         NULL,
    missed_protocol_points    TEXT         NULL,   -- JSON array
    time_taken_seconds        DECIMAL(6, 2) NULL,
    expected_seconds          DECIMAL(6, 2) NULL,
    resources_used            INTEGER      NULL,
    resources_optimal         INTEGER      NULL,
    ethical_score_0_to_10     DECIMAL(4, 2) NULL,
    clinical_accuracy_score   INTEGER      NOT NULL,
    response_time_score       INTEGER      NOT NULL,
    response_efficiency_score INTEGER      NOT NULL,
    ethical_reasoning_score   INTEGER      NOT NULL,
    decision_total            INTEGER      NOT NULL,
    judgment_source            VARCHAR(20) NULL, -- rag_llm / rubric_fallback
    created_at                 DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (attempt_id) REFERENCES attempts (id)
);

CREATE INDEX idx_decision_scores_attempt ON decision_scores (attempt_id);
