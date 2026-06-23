SET NAMES utf8mb4;

ALTER TABLE rule_engine_linked_plan
    ADD COLUMN IF NOT EXISTS expires_at DATETIME NULL,
    ADD COLUMN IF NOT EXISTS closed_at DATETIME NULL;

CREATE TABLE IF NOT EXISTS rule_engine_delta_item (
    delta_item_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    linked_plan_id BIGINT UNSIGNED NOT NULL,
    linked_step_id BIGINT UNSIGNED NULL,
    step_key VARCHAR(100) NOT NULL,
    step_order INT UNSIGNED NOT NULL,
    target_table VARCHAR(32) NOT NULL,
    source_row_id BIGINT UNSIGNED NOT NULL,
    source_row_hash CHAR(64) NULL,
    delta_type VARCHAR(32) NOT NULL DEFAULT 'preview_update',
    before_json JSON NULL,
    after_json JSON NULL,
    delta_json JSON NULL,
    preview_fingerprint CHAR(16) NULL,
    delta_status VARCHAR(50) NOT NULL DEFAULT 'pending',
    expires_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NULL ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (delta_item_id),
    INDEX idx_rule_engine_delta_plan_order (linked_plan_id, step_order),
    INDEX idx_rule_engine_delta_plan_row (linked_plan_id, source_row_id),
    INDEX idx_rule_engine_delta_plan_hash (linked_plan_id, source_row_hash),
    INDEX idx_rule_engine_delta_plan_step (linked_plan_id, linked_step_id),
    INDEX idx_rule_engine_delta_status_expiry (delta_status, expires_at),
    INDEX idx_rule_engine_delta_expiry (expires_at),
    CONSTRAINT fk_rule_engine_delta_plan
        FOREIGN KEY (linked_plan_id) REFERENCES rule_engine_linked_plan(linked_plan_id)
        ON UPDATE RESTRICT
        ON DELETE CASCADE,
    CONSTRAINT fk_rule_engine_delta_step
        FOREIGN KEY (linked_step_id) REFERENCES rule_engine_linked_plan_step(linked_step_id)
        ON UPDATE RESTRICT
        ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
