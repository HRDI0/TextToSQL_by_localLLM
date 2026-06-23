SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS import_batch (
    batch_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    advertiser_code VARCHAR(100) NOT NULL,
    report_month VARCHAR(20) NOT NULL,
    batch_name VARCHAR(255) NULL,
    uploaded_by VARCHAR(100) NULL,
    uploaded_at DATETIME NOT NULL,
    status VARCHAR(30) NOT NULL,
    message TEXT NULL,
    PRIMARY KEY (batch_id),
    INDEX idx_import_batch_month (advertiser_code, report_month, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS media_source (
    media_source_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    media_code VARCHAR(100) NOT NULL,
    media_name VARCHAR(100) NOT NULL,
    media_category VARCHAR(50) NULL,
    platform_type VARCHAR(50) NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (media_source_id),
    UNIQUE KEY uk_media_source_code (media_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS source_schema_profile (
    schema_profile_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    media_source_id BIGINT UNSIGNED NOT NULL,
    profile_name VARCHAR(100) NOT NULL,
    file_type VARCHAR(20) NULL,
    encoding VARCHAR(50) NULL,
    delimiter VARCHAR(20) NULL,
    header_row_no INT NULL,
    date_format VARCHAR(50) NULL,
    has_metadata_rows BOOLEAN NOT NULL DEFAULT FALSE,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (schema_profile_id),
    UNIQUE KEY uk_schema_profile_media_name (media_source_id, profile_name),
    CONSTRAINT fk_schema_profile_media
        FOREIGN KEY (media_source_id) REFERENCES media_source(media_source_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS standard_field (
    standard_field_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    field_code VARCHAR(100) NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    data_type VARCHAR(50) NOT NULL,
    field_role VARCHAR(50) NULL,
    description TEXT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (standard_field_id),
    UNIQUE KEY uk_standard_field_code (field_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS source_field_mapping (
    mapping_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    schema_profile_id BIGINT UNSIGNED NOT NULL,
    source_field_name VARCHAR(255) NOT NULL,
    standard_field_id BIGINT UNSIGNED NOT NULL,
    cast_type VARCHAR(50) NULL,
    transform_rule JSON NULL,
    required BOOLEAN NOT NULL DEFAULT FALSE,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (mapping_id),
    UNIQUE KEY uk_source_field_mapping (schema_profile_id, source_field_name, standard_field_id),
    INDEX idx_source_field_mapping_source (source_field_name),
    CONSTRAINT fk_source_field_mapping_profile
        FOREIGN KEY (schema_profile_id) REFERENCES source_schema_profile(schema_profile_id)
        ON UPDATE RESTRICT
        ON DELETE CASCADE,
    CONSTRAINT fk_source_field_mapping_standard
        FOREIGN KEY (standard_field_id) REFERENCES standard_field(standard_field_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS dim_date (
    date_id INT NOT NULL,
    date_value DATE NOT NULL,
    year_no INT NULL,
    month_no INT NULL,
    week_label VARCHAR(50) NULL,
    weekday_no INT NULL,
    weekday_name VARCHAR(20) NULL,
    PRIMARY KEY (date_id),
    UNIQUE KEY uk_dim_date_value (date_value)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS dim_media (
    media_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    media_source_id BIGINT UNSIGNED NULL,
    media_name VARCHAR(100) NULL,
    ad_media VARCHAR(100) NULL,
    ad_type VARCHAR(100) NULL,
    channel_group VARCHAR(100) NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (media_id),
    UNIQUE KEY uk_dim_media_source (media_source_id),
    CONSTRAINT fk_dim_media_source
        FOREIGN KEY (media_source_id) REFERENCES media_source(media_source_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS dim_campaign (
    campaign_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    media_source_id BIGINT UNSIGNED NULL,
    campaign_name TEXT NOT NULL,
    campaign_code VARCHAR(255) NULL,
    campaign_hash CHAR(64) NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (campaign_id),
    UNIQUE KEY uk_dim_campaign_hash (campaign_hash),
    INDEX idx_dim_campaign_name (campaign_name(255)),
    CONSTRAINT fk_dim_campaign_media
        FOREIGN KEY (media_source_id) REFERENCES media_source(media_source_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS dim_ad_group (
    ad_group_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    media_source_id BIGINT UNSIGNED NULL,
    ad_group_name TEXT NULL,
    ad_group_code VARCHAR(255) NULL,
    ad_group_hash CHAR(64) NOT NULL,
    PRIMARY KEY (ad_group_id),
    UNIQUE KEY uk_dim_ad_group_hash (ad_group_hash),
    INDEX idx_dim_ad_group_name (ad_group_name(255)),
    CONSTRAINT fk_dim_ad_group_media
        FOREIGN KEY (media_source_id) REFERENCES media_source(media_source_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS dim_creative (
    creative_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    media_source_id BIGINT UNSIGNED NULL,
    creative_name TEXT NULL,
    creative_code VARCHAR(255) NULL,
    page_number VARCHAR(100) NULL,
    plan_category VARCHAR(100) NULL,
    plan_category2 VARCHAR(100) NULL,
    creative_hash CHAR(64) NOT NULL,
    PRIMARY KEY (creative_id),
    UNIQUE KEY uk_dim_creative_hash (creative_hash),
    INDEX idx_dim_creative_name (creative_name(255)),
    CONSTRAINT fk_dim_creative_media
        FOREIGN KEY (media_source_id) REFERENCES media_source(media_source_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS dim_device (
    device_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    device_raw VARCHAR(100) NULL,
    device_group VARCHAR(50) NULL,
    device_label VARCHAR(100) NULL,
    PRIMARY KEY (device_id),
    UNIQUE KEY uk_dim_device_raw (device_raw)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS dim_event (
    event_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    event_name VARCHAR(255) NOT NULL,
    event_category VARCHAR(100) NULL,
    conversion_type VARCHAR(100) NULL,
    is_conversion BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (event_id),
    UNIQUE KEY uk_dim_event_name (event_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS metric_registry (
    metric_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    metric_code VARCHAR(100) NOT NULL,
    metric_name VARCHAR(100) NOT NULL,
    metric_group VARCHAR(100) NULL,
    data_type VARCHAR(50) NULL,
    default_unit VARCHAR(50) NULL,
    PRIMARY KEY (metric_id),
    UNIQUE KEY uk_metric_registry_code (metric_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS source_file (
    file_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    batch_id BIGINT UNSIGNED NOT NULL,
    media_source_id BIGINT UNSIGNED NULL,
    source_part VARCHAR(50) NULL,
    source_group VARCHAR(50) NULL,
    source_name VARCHAR(100) NULL,
    original_file_name VARCHAR(255) NOT NULL,
    file_path TEXT NULL,
    file_type VARCHAR(20) NULL,
    sheet_name VARCHAR(255) NULL,
    encoding VARCHAR(50) NULL,
    delimiter VARCHAR(20) NULL,
    header_row_no INT NULL,
    total_rows INT NULL,
    imported_rows INT NULL,
    import_status VARCHAR(30) NULL,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (file_id),
    INDEX idx_source_file_batch (batch_id, source_part, source_group, source_name),
    INDEX idx_source_file_media (media_source_id),
    INDEX idx_source_file_path (file_path(255)),
    CONSTRAINT fk_source_file_batch
        FOREIGN KEY (batch_id) REFERENCES import_batch(batch_id)
        ON UPDATE RESTRICT
        ON DELETE CASCADE,
    CONSTRAINT fk_source_file_media
        FOREIGN KEY (media_source_id) REFERENCES media_source(media_source_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS raw_record (
    raw_record_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    file_id BIGINT UNSIGNED NOT NULL,
    row_no INT NOT NULL,
    raw_payload JSON NOT NULL,
    raw_hash CHAR(64) NULL,
    parse_status VARCHAR(30) NULL,
    parse_error TEXT NULL,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (raw_record_id),
    UNIQUE KEY uk_raw_record_file_row (file_id, row_no),
    INDEX idx_raw_record_file (file_id, row_no),
    INDEX idx_raw_record_hash (raw_hash),
    CONSTRAINT fk_raw_record_file
        FOREIGN KEY (file_id) REFERENCES source_file(file_id)
        ON UPDATE RESTRICT
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS fact_ad_daily (
    ad_fact_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    raw_record_id BIGINT UNSIGNED NOT NULL,
    file_id BIGINT UNSIGNED NOT NULL,
    date_id INT NOT NULL,
    media_id BIGINT UNSIGNED NULL,
    campaign_id BIGINT UNSIGNED NULL,
    ad_group_id BIGINT UNSIGNED NULL,
    creative_id BIGINT UNSIGNED NULL,
    device_id BIGINT UNSIGNED NULL,
    source_part VARCHAR(50) NULL,
    source_group VARCHAR(50) NULL,
    source_name VARCHAR(100) NULL,
    impressions DECIMAL(18, 2) NOT NULL DEFAULT 0,
    clicks DECIMAL(18, 2) NOT NULL DEFAULT 0,
    cost_raw DECIMAL(18, 2) NOT NULL DEFAULT 0,
    cost_currency VARCHAR(20) NULL,
    ctr DECIMAL(18, 6) NULL,
    landing_url TEXT NULL,
    additional_metrics JSON NULL,
    normalized_status VARCHAR(30) NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (ad_fact_id),
    UNIQUE KEY uk_fact_ad_raw_record (raw_record_id),
    INDEX idx_fact_ad_daily_main (date_id, media_id, campaign_id, device_id),
    INDEX idx_fact_ad_file (file_id),
    CONSTRAINT fk_fact_ad_raw_record
        FOREIGN KEY (raw_record_id) REFERENCES raw_record(raw_record_id)
        ON UPDATE RESTRICT
        ON DELETE CASCADE,
    CONSTRAINT fk_fact_ad_file
        FOREIGN KEY (file_id) REFERENCES source_file(file_id)
        ON UPDATE RESTRICT
        ON DELETE CASCADE,
    CONSTRAINT fk_fact_ad_date
        FOREIGN KEY (date_id) REFERENCES dim_date(date_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    CONSTRAINT fk_fact_ad_media
        FOREIGN KEY (media_id) REFERENCES dim_media(media_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    CONSTRAINT fk_fact_ad_campaign
        FOREIGN KEY (campaign_id) REFERENCES dim_campaign(campaign_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    CONSTRAINT fk_fact_ad_group
        FOREIGN KEY (ad_group_id) REFERENCES dim_ad_group(ad_group_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    CONSTRAINT fk_fact_ad_creative
        FOREIGN KEY (creative_id) REFERENCES dim_creative(creative_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    CONSTRAINT fk_fact_ad_device
        FOREIGN KEY (device_id) REFERENCES dim_device(device_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS fact_ga_daily (
    ga_fact_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    raw_record_id BIGINT UNSIGNED NOT NULL,
    file_id BIGINT UNSIGNED NOT NULL,
    date_id INT NOT NULL,
    media_id BIGINT UNSIGNED NULL,
    campaign_id BIGINT UNSIGNED NULL,
    creative_id BIGINT UNSIGNED NULL,
    device_id BIGINT UNSIGNED NULL,
    event_id BIGINT UNSIGNED NULL,
    session_source_medium VARCHAR(255) NULL,
    session_channel_group VARCHAR(100) NULL,
    session_campaign TEXT NULL,
    session_content TEXT NULL,
    session_term TEXT NULL,
    sessions DECIMAL(18, 2) NOT NULL DEFAULT 0,
    users DECIMAL(18, 2) NOT NULL DEFAULT 0,
    key_events DECIMAL(18, 2) NOT NULL DEFAULT 0,
    source_part VARCHAR(50) NULL,
    source_group VARCHAR(50) NULL,
    source_name VARCHAR(100) NULL,
    additional_metrics JSON NULL,
    normalized_status VARCHAR(30) NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (ga_fact_id),
    UNIQUE KEY uk_fact_ga_raw_record (raw_record_id),
    INDEX idx_fact_ga_daily_main (date_id, media_id, campaign_id, event_id, device_id),
    INDEX idx_fact_ga_file (file_id),
    CONSTRAINT fk_fact_ga_raw_record
        FOREIGN KEY (raw_record_id) REFERENCES raw_record(raw_record_id)
        ON UPDATE RESTRICT
        ON DELETE CASCADE,
    CONSTRAINT fk_fact_ga_file
        FOREIGN KEY (file_id) REFERENCES source_file(file_id)
        ON UPDATE RESTRICT
        ON DELETE CASCADE,
    CONSTRAINT fk_fact_ga_date
        FOREIGN KEY (date_id) REFERENCES dim_date(date_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    CONSTRAINT fk_fact_ga_media
        FOREIGN KEY (media_id) REFERENCES dim_media(media_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    CONSTRAINT fk_fact_ga_campaign
        FOREIGN KEY (campaign_id) REFERENCES dim_campaign(campaign_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    CONSTRAINT fk_fact_ga_creative
        FOREIGN KEY (creative_id) REFERENCES dim_creative(creative_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    CONSTRAINT fk_fact_ga_device
        FOREIGN KEY (device_id) REFERENCES dim_device(device_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    CONSTRAINT fk_fact_ga_event
        FOREIGN KEY (event_id) REFERENCES dim_event(event_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS fact_metric_daily (
    metric_fact_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    raw_record_id BIGINT UNSIGNED NULL,
    file_id BIGINT UNSIGNED NULL,
    date_id INT NULL,
    media_id BIGINT UNSIGNED NULL,
    campaign_id BIGINT UNSIGNED NULL,
    ad_group_id BIGINT UNSIGNED NULL,
    creative_id BIGINT UNSIGNED NULL,
    device_id BIGINT UNSIGNED NULL,
    event_id BIGINT UNSIGNED NULL,
    metric_id BIGINT UNSIGNED NOT NULL,
    metric_value DECIMAL(18, 6) NULL,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (metric_fact_id),
    INDEX idx_fact_metric_main (date_id, media_id, campaign_id, metric_id),
    CONSTRAINT fk_fact_metric_raw_record
        FOREIGN KEY (raw_record_id) REFERENCES raw_record(raw_record_id)
        ON UPDATE RESTRICT
        ON DELETE CASCADE,
    CONSTRAINT fk_fact_metric_file
        FOREIGN KEY (file_id) REFERENCES source_file(file_id)
        ON UPDATE RESTRICT
        ON DELETE CASCADE,
    CONSTRAINT fk_fact_metric_date
        FOREIGN KEY (date_id) REFERENCES dim_date(date_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    CONSTRAINT fk_fact_metric_media
        FOREIGN KEY (media_id) REFERENCES dim_media(media_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    CONSTRAINT fk_fact_metric_metric
        FOREIGN KEY (metric_id) REFERENCES metric_registry(metric_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS report_daily_row (
    report_row_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    report_month VARCHAR(20) NULL,
    report_date DATE NULL,
    source_part VARCHAR(50) NULL,
    source_group VARCHAR(50) NULL,
    source_name VARCHAR(100) NULL,
    type_group VARCHAR(100) NULL,
    ad_media VARCHAR(100) NULL,
    ad_type VARCHAR(100) NULL,
    device_group VARCHAR(50) NULL,
    sa_group VARCHAR(100) NULL,
    non_ad_channel VARCHAR(100) NULL,
    summary_group VARCHAR(100) NULL,
    summary_group2 VARCHAR(100) NULL,
    ad_flag VARCHAR(50) NULL,
    media_raw VARCHAR(100) NULL,
    device_raw VARCHAR(100) NULL,
    campaign_name TEXT NULL,
    ad_group_name TEXT NULL,
    creative_name_raw TEXT NULL,
    creative_name2 TEXT NULL,
    creative_name3 TEXT NULL,
    keyword_name TEXT NULL,
    plan_category VARCHAR(100) NULL,
    plan_category2 VARCHAR(100) NULL,
    page_number VARCHAR(100) NULL,
    triple_group VARCHAR(100) NULL,
    device2 VARCHAR(50) NULL,
    impressions DECIMAL(18, 2) NOT NULL DEFAULT 0,
    clicks DECIMAL(18, 2) NOT NULL DEFAULT 0,
    cost_raw DECIMAL(18, 2) NOT NULL DEFAULT 0,
    markup DECIMAL(18, 6) NULL,
    cost_adjusted DECIMAL(18, 2) NULL,
    sessions DECIMAL(18, 2) NOT NULL DEFAULT 0,
    users DECIMAL(18, 2) NOT NULL DEFAULT 0,
    start_usim DECIMAL(18, 2) NOT NULL DEFAULT 0,
    start_esim DECIMAL(18, 2) NOT NULL DEFAULT 0,
    start_total DECIMAL(18, 2) NOT NULL DEFAULT 0,
    complete_usim DECIMAL(18, 2) NOT NULL DEFAULT 0,
    complete_esim DECIMAL(18, 2) NOT NULL DEFAULT 0,
    complete_total DECIMAL(18, 2) NOT NULL DEFAULT 0,
    kt_shop DECIMAL(18, 2) NOT NULL DEFAULT 0,
    kt_shop_non_ad DECIMAL(18, 2) NOT NULL DEFAULT 0,
    source_ad_fact_id BIGINT UNSIGNED NULL,
    source_ga_fact_id BIGINT UNSIGNED NULL,
    applied_rule_ids JSON NULL,
    transform_status VARCHAR(30) NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (report_row_id),
    INDEX idx_report_daily_row_main (report_date, source_part, ad_media, ad_type, device_group),
    INDEX idx_report_daily_campaign (campaign_name(255)),
    CONSTRAINT fk_report_ad_fact
        FOREIGN KEY (source_ad_fact_id) REFERENCES fact_ad_daily(ad_fact_id)
        ON UPDATE RESTRICT
        ON DELETE SET NULL,
    CONSTRAINT fk_report_ga_fact
        FOREIGN KEY (source_ga_fact_id) REFERENCES fact_ga_daily(ga_fact_id)
        ON UPDATE RESTRICT
        ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rule_apply_audit (
    audit_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id VARCHAR(100) NOT NULL,
    rule_id VARCHAR(100) NOT NULL,
    target_table VARCHAR(100) NOT NULL,
    target_pk BIGINT UNSIGNED NOT NULL,
    target_field VARCHAR(100) NOT NULL,
    before_value TEXT NULL,
    after_value TEXT NULL,
    applied_at DATETIME NOT NULL,
    PRIMARY KEY (audit_id),
    INDEX idx_rule_apply_audit_target (target_table, target_pk, target_field),
    INDEX idx_rule_apply_audit_run (run_id, rule_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
