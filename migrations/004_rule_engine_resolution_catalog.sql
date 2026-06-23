CREATE TABLE IF NOT EXISTS rule_engine_catalog_refresh_log (
    refresh_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    refresh_type VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    row_count INT NOT NULL DEFAULT 0,
    error_message TEXT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rule_engine_column_catalog (
    target_table VARCHAR(64) NOT NULL,
    column_name VARCHAR(255) NOT NULL,
    normalized_column_name VARCHAR(255) NOT NULL,
    semantic_role VARCHAR(64) NULL,
    distinct_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
    last_refreshed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (target_table, column_name),
    KEY idx_rule_engine_column_catalog_normalized (normalized_column_name),
    KEY idx_rule_engine_column_catalog_role (semantic_role)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rule_engine_value_catalog (
    target_table VARCHAR(64) NOT NULL,
    column_name VARCHAR(255) NOT NULL,
    normalized_value VARCHAR(512) NOT NULL,
    raw_value TEXT NOT NULL,
    frequency BIGINT UNSIGNED NOT NULL DEFAULT 0,
    last_refreshed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (target_table, column_name, normalized_value),
    KEY idx_rule_engine_value_catalog_lookup (target_table, column_name, frequency),
    KEY idx_rule_engine_value_catalog_normalized (normalized_value)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
