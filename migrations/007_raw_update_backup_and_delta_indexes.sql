SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS rule_engine_raw_update_backup (
    backup_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    backup_scope CHAR(16) NOT NULL,
    target_table VARCHAR(32) NOT NULL,
    source_row_id BIGINT UNSIGNED NOT NULL,
    source_row_hash CHAR(64) NULL,
    before_json JSON NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (backup_id),
    UNIQUE KEY uk_rule_engine_raw_update_backup_scope_row (backup_scope, target_table, source_row_id),
    INDEX idx_rule_engine_raw_update_backup_table_row (target_table, source_row_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_rule_engine_delta_overlay_lookup
    ON rule_engine_delta_item (linked_plan_id, target_table, source_row_id, delta_status, step_order);
