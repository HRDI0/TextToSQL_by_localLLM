SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS rule_engine_linked_plan (
    linked_plan_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    request_fingerprint CHAR(16) NOT NULL,
    step_count INT UNSIGNED NOT NULL DEFAULT 0,
    dependent_step_count INT UNSIGNED NOT NULL DEFAULT 0,
    validation_status VARCHAR(50) NOT NULL DEFAULT 'planned',
    validation_errors JSON NULL,
    plan_status VARCHAR(50) NOT NULL DEFAULT 'planned',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NULL ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (linked_plan_id),
    INDEX idx_rule_engine_linked_plan_fingerprint (request_fingerprint),
    INDEX idx_rule_engine_linked_plan_status (plan_status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rule_engine_linked_plan_step (
    linked_step_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    linked_plan_id BIGINT UNSIGNED NOT NULL,
    step_order INT UNSIGNED NOT NULL,
    step_key VARCHAR(100) NOT NULL,
    intent_type VARCHAR(64) NULL,
    dependency_type VARCHAR(32) NOT NULL DEFAULT 'independent',
    depends_on_json JSON NULL,
    target_table VARCHAR(32) NULL,
    step_status VARCHAR(50) NOT NULL DEFAULT 'planned',
    sql_fingerprint CHAR(16) NULL,
    validation_status VARCHAR(50) NULL,
    preview_fingerprint CHAR(16) NULL,
    approval_status VARCHAR(50) NOT NULL DEFAULT 'pending',
    execution_status VARCHAR(50) NOT NULL DEFAULT 'not_started',
    plan_json JSON NULL,
    result_json JSON NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NULL ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (linked_step_id),
    UNIQUE KEY uk_rule_engine_linked_plan_step (linked_plan_id, step_key),
    INDEX idx_rule_engine_linked_plan_step_order (linked_plan_id, step_order),
    INDEX idx_rule_engine_linked_plan_step_status (step_status, validation_status, approval_status),
    CONSTRAINT fk_rule_engine_linked_plan_step_plan
        FOREIGN KEY (linked_plan_id) REFERENCES rule_engine_linked_plan(linked_plan_id)
        ON UPDATE RESTRICT
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
