SET NAMES utf8mb4;

SET @need_da_row_id := (
    SELECT COUNT(*) = 0
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'DA' AND COLUMN_NAME = 'row_id'
);
SET @sql := IF(
    @need_da_row_id,
    'ALTER TABLE `DA` ADD COLUMN `row_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY FIRST',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @need_sa_row_id := (
    SELECT COUNT(*) = 0
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'SA' AND COLUMN_NAME = 'row_id'
);
SET @sql := IF(
    @need_sa_row_id,
    'ALTER TABLE `SA` ADD COLUMN `row_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY FIRST',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

ALTER TABLE `DA`
    ADD COLUMN IF NOT EXISTS `import_batch_id` BIGINT UNSIGNED NULL AFTER `row_id`,
    ADD COLUMN IF NOT EXISTS `source_row_hash` CHAR(64) NULL AFTER `import_batch_id`;

ALTER TABLE `SA`
    ADD COLUMN IF NOT EXISTS `import_batch_id` BIGINT UNSIGNED NULL AFTER `row_id`,
    ADD COLUMN IF NOT EXISTS `source_row_hash` CHAR(64) NULL AFTER `import_batch_id`;

UPDATE `DA`
SET `source_row_hash` = SHA2(CONCAT_WS('|',
    `source_channel`, COALESCE(`날짜`, ''), COALESCE(`디바이스`, ''), COALESCE(`세션 소스/매체`, ''),
    COALESCE(`세션 캠페인`, ''), COALESCE(`세션 수동 광고 콘텐츠`, ''), COALESCE(`세션 수동 검색어`, ''),
    COALESCE(`이벤트 이름`, ''), COALESCE(`세션수`, ''), COALESCE(`캠페인 유형`, ''), COALESCE(`캠페인`, ''),
    COALESCE(`광고 그룹`, ''), COALESCE(`광고 최종 도착 URL`, ''), COALESCE(`노출수`, ''), COALESCE(`클릭수`, ''),
    COALESCE(`통화 코드`, ''), COALESCE(`비용`, ''), COALESCE(`광고 소재`, ''), COALESCE(`광고 소재 ID`, ''),
    COALESCE(`광고 그룹 ID`, ''), COALESCE(`캠페인 ID`, ''), COALESCE(`기간`, ''), COALESCE(`시작일`, ''), COALESCE(`종료일`, '')
), 256)
WHERE `source_row_hash` IS NULL;

UPDATE `SA`
SET `source_row_hash` = SHA2(CONCAT_WS('|',
    `source_channel`, COALESCE(`날짜`, ''), COALESCE(`세션 소스/매체`, ''), COALESCE(`세션 캠페인`, ''),
    COALESCE(`세션 수동 광고 콘텐츠`, ''), COALESCE(`세션 수동 검색어`, ''), COALESCE(`디바이스`, ''), COALESCE(`세션수`, ''),
    COALESCE(`이벤트 이름`, ''), COALESCE(`세션 기본 채널 그룹`, ''), COALESCE(`주요 이벤트`, ''), COALESCE(`총 사용자`, ''),
    COALESCE(`캠페인 유형`, ''), COALESCE(`캠페인`, ''), COALESCE(`광고 그룹`, ''), COALESCE(`노출수`, ''),
    COALESCE(`클릭수`, ''), COALESCE(`비용`, ''), COALESCE(`통화 코드`, ''), COALESCE(`광고상품`, ''),
    COALESCE(`PC/모바일`, ''), COALESCE(`광고소재요소`, ''), COALESCE(`광고라인`, ''), COALESCE(`기간`, ''), COALESCE(`클릭률(%)`, '')
), 256)
WHERE `source_row_hash` IS NULL;

CREATE INDEX IF NOT EXISTS idx_da_source_channel ON `DA` (`source_channel`);
CREATE INDEX IF NOT EXISTS idx_sa_source_channel ON `SA` (`source_channel`);
CREATE INDEX IF NOT EXISTS idx_da_source_row_hash ON `DA` (`source_row_hash`);
CREATE INDEX IF NOT EXISTS idx_sa_source_row_hash ON `SA` (`source_row_hash`);
CREATE INDEX IF NOT EXISTS idx_da_campaign_prefix ON `DA` (`캠페인`(255));
CREATE INDEX IF NOT EXISTS idx_sa_campaign_prefix ON `SA` (`캠페인`(255));
CREATE INDEX IF NOT EXISTS idx_da_ad_group_prefix ON `DA` (`광고 그룹`(255));
CREATE INDEX IF NOT EXISTS idx_sa_ad_group_prefix ON `SA` (`광고 그룹`(255));
CREATE INDEX IF NOT EXISTS idx_da_event_prefix ON `DA` (`이벤트 이름`(255));
CREATE INDEX IF NOT EXISTS idx_sa_event_prefix ON `SA` (`이벤트 이름`(255));

CREATE TABLE IF NOT EXISTS rule_engine_source_channel_catalog (
    catalog_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    target_table VARCHAR(32) NOT NULL,
    source_channel VARCHAR(255) NOT NULL,
    source_file_code VARCHAR(255) NOT NULL,
    media_name VARCHAR(100) NULL,
    ad_type VARCHAR(100) NULL,
    data_origin VARCHAR(100) NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (catalog_id),
    UNIQUE KEY uk_rule_engine_source_channel_catalog (target_table, source_channel),
    INDEX idx_rule_engine_source_channel_catalog_media (media_name, ad_type, data_origin)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rule_engine_protected_column_policy (
    policy_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    target_table VARCHAR(32) NOT NULL,
    column_name VARCHAR(255) NOT NULL,
    protection_level VARCHAR(50) NOT NULL DEFAULT 'block_update',
    reason VARCHAR(255) NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (policy_id),
    UNIQUE KEY uk_rule_engine_protected_column_policy (target_table, column_name, protection_level),
    INDEX idx_rule_engine_protected_column_policy_column (column_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rule_engine_derived_value (
    derived_value_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    target_table VARCHAR(32) NOT NULL,
    source_row_id BIGINT UNSIGNED NOT NULL,
    source_row_hash CHAR(64) NULL,
    derived_key VARCHAR(255) NOT NULL,
    derived_value TEXT NULL,
    rule_id VARCHAR(255) NULL,
    request_id BIGINT UNSIGNED NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NULL ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (derived_value_id),
    UNIQUE KEY uk_rule_engine_derived_value (target_table, source_row_id, derived_key),
    INDEX idx_rule_engine_derived_value_rule (rule_id),
    INDEX idx_rule_engine_derived_value_request (request_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rule_engine_execution_log (
    execution_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    request_text TEXT NOT NULL,
    selection_text TEXT NULL,
    modification_text TEXT NULL,
    model_label VARCHAR(255) NULL,
    generated_ir JSON NULL,
    generated_sql TEXT NULL,
    sql_params_json JSON NULL,
    sql_fingerprint CHAR(16) NULL,
    preview_row_count INT UNSIGNED NOT NULL DEFAULT 0,
    affected_row_count INT UNSIGNED NULL,
    approval_status VARCHAR(50) NOT NULL DEFAULT 'previewed',
    approved_at DATETIME NULL,
    executed_at DATETIME NULL,
    rollback_sql TEXT NULL,
    rollback_params_json JSON NULL,
    error_message TEXT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (execution_id),
    INDEX idx_rule_engine_execution_log_status (approval_status, created_at),
    INDEX idx_rule_engine_execution_log_fingerprint (sql_fingerprint)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE rule_engine_metric_definition
    ADD COLUMN IF NOT EXISTS source_channel_scope JSON NULL AFTER target_table,
    ADD COLUMN IF NOT EXISTS event_filter JSON NULL AFTER source_channel_scope,
    ADD COLUMN IF NOT EXISTS business_definition TEXT NULL AFTER event_filter;

CREATE TABLE IF NOT EXISTS DA_canonical (
    canonical_row_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    source_row_id BIGINT UNSIGNED NOT NULL,
    source_row_hash CHAR(64) NULL,
    source_channel VARCHAR(255) NOT NULL,
    report_date DATE NULL,
    device_type_std VARCHAR(50) NULL,
    session_source_medium VARCHAR(255) NULL,
    campaign_name VARCHAR(255) NULL,
    ad_group_name VARCHAR(255) NULL,
    creative_name VARCHAR(255) NULL,
    event_name VARCHAR(255) NULL,
    impressions DECIMAL(18, 2) NULL,
    clicks DECIMAL(18, 2) NULL,
    cost DECIMAL(18, 2) NULL,
    sessions DECIMAL(18, 2) NULL,
    ctr DECIMAL(18, 6) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (canonical_row_id),
    UNIQUE KEY uk_da_canonical_source_row (source_row_id),
    INDEX idx_da_canonical_scope (source_channel, report_date),
    INDEX idx_da_canonical_campaign (campaign_name),
    INDEX idx_da_canonical_event (event_name),
    INDEX idx_da_canonical_device (device_type_std)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS SA_canonical (
    canonical_row_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    source_row_id BIGINT UNSIGNED NOT NULL,
    source_row_hash CHAR(64) NULL,
    source_channel VARCHAR(255) NOT NULL,
    report_date DATE NULL,
    device_type_std VARCHAR(50) NULL,
    session_source_medium VARCHAR(255) NULL,
    session_campaign VARCHAR(255) NULL,
    session_channel_group VARCHAR(255) NULL,
    campaign_name VARCHAR(255) NULL,
    ad_group_name VARCHAR(255) NULL,
    event_name VARCHAR(255) NULL,
    impressions DECIMAL(18, 2) NULL,
    clicks DECIMAL(18, 2) NULL,
    cost DECIMAL(18, 2) NULL,
    sessions DECIMAL(18, 2) NULL,
    total_users DECIMAL(18, 2) NULL,
    key_events DECIMAL(18, 2) NULL,
    ctr DECIMAL(18, 6) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (canonical_row_id),
    UNIQUE KEY uk_sa_canonical_source_row (source_row_id),
    INDEX idx_sa_canonical_scope (source_channel, report_date),
    INDEX idx_sa_canonical_campaign (campaign_name),
    INDEX idx_sa_canonical_event (event_name),
    INDEX idx_sa_canonical_device (device_type_std),
    INDEX idx_sa_canonical_channel_group (session_channel_group)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE DA_canonical
    MODIFY COLUMN impressions DECIMAL(18, 2) NULL,
    MODIFY COLUMN clicks DECIMAL(18, 2) NULL,
    MODIFY COLUMN sessions DECIMAL(18, 2) NULL;

ALTER TABLE SA_canonical
    MODIFY COLUMN impressions DECIMAL(18, 2) NULL,
    MODIFY COLUMN clicks DECIMAL(18, 2) NULL,
    MODIFY COLUMN sessions DECIMAL(18, 2) NULL,
    MODIFY COLUMN total_users DECIMAL(18, 2) NULL;

INSERT INTO DA_canonical (
    source_row_id, source_row_hash, source_channel, report_date, device_type_std,
    session_source_medium, campaign_name, ad_group_name, creative_name, event_name,
    impressions, clicks, cost, sessions, ctr
)
SELECT
    `row_id`, `source_row_hash`, `source_channel`,
    CASE
        WHEN NULLIF(`날짜`, '') REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN STR_TO_DATE(`날짜`, '%Y-%m-%d')
        WHEN NULLIF(`날짜`, '') REGEXP '^[0-9]{4}\\.\\s*[0-9]{1,2}\\.\\s*[0-9]{1,2}\\.?$' THEN STR_TO_DATE(TRIM(BOTH '.' FROM REGEXP_REPLACE(`날짜`, '\\s+', '')), '%Y.%m.%d')
        ELSE NULL
    END AS report_date,
    CASE
        WHEN LOWER(COALESCE(`디바이스`, '')) IN ('mobile', '모바일', '휴대전화', 'android', 'ios') THEN 'mobile'
        WHEN LOWER(COALESCE(`디바이스`, '')) IN ('desktop', 'pc', '컴퓨터') THEN 'desktop'
        WHEN LOWER(COALESCE(`디바이스`, '')) IN ('tablet', '태블릿') THEN 'tablet'
        ELSE NULLIF(`디바이스`, '')
    END AS device_type_std,
    NULLIF(`세션 소스/매체`, ''),
    NULLIF(COALESCE(NULLIF(`캠페인`, ''), NULLIF(`세션 캠페인`, '')), ''),
    NULLIF(`광고 그룹`, ''),
    NULLIF(COALESCE(NULLIF(`광고 소재`, ''), NULLIF(`세션 수동 광고 콘텐츠`, '')), ''),
    NULLIF(`이벤트 이름`, ''),
    CAST(NULLIF(REPLACE(`노출수`, ',', ''), '') AS DECIMAL(18, 2)),
    CAST(NULLIF(REPLACE(`클릭수`, ',', ''), '') AS DECIMAL(18, 2)),
    CAST(NULLIF(REPLACE(`비용`, ',', ''), '') AS DECIMAL(18, 2)),
    CAST(NULLIF(REPLACE(`세션수`, ',', ''), '') AS DECIMAL(18, 2)),
    NULL
FROM `DA`
ON DUPLICATE KEY UPDATE
    source_row_hash = VALUES(source_row_hash),
    report_date = VALUES(report_date),
    device_type_std = VALUES(device_type_std),
    impressions = VALUES(impressions),
    clicks = VALUES(clicks),
    cost = VALUES(cost),
    sessions = VALUES(sessions);

INSERT INTO SA_canonical (
    source_row_id, source_row_hash, source_channel, report_date, device_type_std,
    session_source_medium, session_campaign, session_channel_group, campaign_name, ad_group_name, event_name,
    impressions, clicks, cost, sessions, total_users, key_events, ctr
)
SELECT
    `row_id`, `source_row_hash`, `source_channel`,
    CASE
        WHEN NULLIF(`날짜`, '') REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN STR_TO_DATE(`날짜`, '%Y-%m-%d')
        WHEN NULLIF(`날짜`, '') REGEXP '^[0-9]{4}\\.\\s*[0-9]{1,2}\\.\\s*[0-9]{1,2}\\.?$' THEN STR_TO_DATE(TRIM(BOTH '.' FROM REGEXP_REPLACE(`날짜`, '\\s+', '')), '%Y.%m.%d')
        ELSE NULL
    END AS report_date,
    CASE
        WHEN LOWER(COALESCE(NULLIF(`디바이스`, ''), NULLIF(`PC/모바일`, ''))) IN ('mobile', '모바일', '휴대전화', 'android', 'ios', 'mo') THEN 'mobile'
        WHEN LOWER(COALESCE(NULLIF(`디바이스`, ''), NULLIF(`PC/모바일`, ''))) IN ('desktop', 'pc', '컴퓨터') THEN 'desktop'
        WHEN LOWER(COALESCE(NULLIF(`디바이스`, ''), NULLIF(`PC/모바일`, ''))) IN ('tablet', '태블릿') THEN 'tablet'
        ELSE NULLIF(COALESCE(NULLIF(`디바이스`, ''), NULLIF(`PC/모바일`, '')), '')
    END AS device_type_std,
    NULLIF(`세션 소스/매체`, ''),
    NULLIF(`세션 캠페인`, ''),
    NULLIF(`세션 기본 채널 그룹`, ''),
    NULLIF(COALESCE(NULLIF(`캠페인`, ''), NULLIF(`세션 캠페인`, '')), ''),
    NULLIF(`광고 그룹`, ''),
    NULLIF(`이벤트 이름`, ''),
    CAST(NULLIF(REPLACE(`노출수`, ',', ''), '') AS DECIMAL(18, 2)),
    CAST(NULLIF(REPLACE(`클릭수`, ',', ''), '') AS DECIMAL(18, 2)),
    CAST(NULLIF(REPLACE(`비용`, ',', ''), '') AS DECIMAL(18, 2)),
    CAST(NULLIF(REPLACE(`세션수`, ',', ''), '') AS DECIMAL(18, 2)),
    CAST(NULLIF(REPLACE(`총 사용자`, ',', ''), '') AS DECIMAL(18, 2)),
    CAST(NULLIF(REPLACE(`주요 이벤트`, ',', ''), '') AS DECIMAL(18, 2)),
    CAST(NULLIF(REPLACE(`클릭률(%)`, ',', ''), '') AS DECIMAL(18, 6))
FROM `SA`
ON DUPLICATE KEY UPDATE
    source_row_hash = VALUES(source_row_hash),
    report_date = VALUES(report_date),
    device_type_std = VALUES(device_type_std),
    impressions = VALUES(impressions),
    clicks = VALUES(clicks),
    cost = VALUES(cost),
    sessions = VALUES(sessions),
    total_users = VALUES(total_users),
    key_events = VALUES(key_events),
    ctr = VALUES(ctr);

INSERT INTO rule_engine_source_channel_catalog (target_table, source_channel, source_file_code, media_name, ad_type, data_origin) VALUES
('DA', 'GA', 'GA', 'GA', 'analytics', 'ga'),
('DA', 'GDN', 'GDN', 'Google', 'display', 'ad_platform'),
('DA', 'GFA', 'GFA', 'Naver', 'display', 'ad_platform'),
('DA', 'KT샵', 'KT샵', 'KT샵', 'owned', 'ga'),
('DA', '디멘드젠', '디멘드젠', 'Google', 'demand_gen', 'ad_platform'),
('DA', '메타', '메타', 'Meta', 'display', 'ad_platform'),
('DA', '카카오', '카카오', 'Kakao', 'display', 'ad_platform'),
('SA', 'GA_KT샵_데이터_0513', 'GA_KT샵_데이터_0513', 'KT샵', 'owned', 'ga'),
('SA', 'GA_SA_데이터_0513', 'GA_SA_데이터_0513', 'GA', 'search', 'ga'),
('SA', 'GA_비광고_KT샵_데이터_0513', 'GA_비광고_KT샵_데이터_0513', 'KT샵', 'non_ad', 'ga'),
('SA', 'GA_비광고_세션_전환_0513', 'GA_비광고_세션_전환_0513', 'GA', 'non_ad', 'ga'),
('SA', 'GA_비광고_총사용자_0513', 'GA_비광고_총사용자_0513', 'GA', 'non_ad', 'ga'),
('SA', 'naver_ksa_0513_columns', 'naver_ksa_0513_columns', 'Naver', 'search', 'ad_platform'),
('SA', '구글_0513', '구글_0513', 'Google', 'search', 'ad_platform'),
('SA', '네이버BSA_0513', '네이버BSA_0513', 'Naver', 'brand_search', 'ad_platform'),
('SA', '네이버BSA_소재요소_0513', '네이버BSA_소재요소_0513', 'Naver', 'brand_search', 'ad_platform'),
('SA', '네이버NSA_0513', '네이버NSA_0513', 'Naver', 'search', 'ad_platform'),
('SA', '카카오BSA_MO_0513', '카카오BSA_MO_0513', 'Kakao', 'brand_search', 'ad_platform'),
('SA', '카카오BSA_PC_0513', '카카오BSA_PC_0513', 'Kakao', 'brand_search', 'ad_platform')
ON DUPLICATE KEY UPDATE
    source_file_code = VALUES(source_file_code), media_name = VALUES(media_name), ad_type = VALUES(ad_type), data_origin = VALUES(data_origin), active = TRUE;

INSERT INTO rule_engine_source_channel_map (user_term, target_table, source_channel, priority) VALUES
('GA 데이터', 'DA', 'GA', 30),
('디멘드젠', 'DA', '디멘드젠', 10),
('디멘드젠 광고', 'DA', '디멘드젠', 10),
('KT샵', 'DA', 'KT샵', 30),
('KT샵 데이터', 'DA', 'KT샵', 30),
('KT샵 데이터', 'SA', 'GA_비광고_KT샵_데이터_0513', 30),
('비광고 KT샵 데이터', 'SA', 'GA_비광고_KT샵_데이터_0513', 20),
('카카오 브랜드검색', 'SA', '카카오BSA_PC_0513', 10),
('카카오 브랜드검색', 'SA', '카카오BSA_MO_0513', 10),
('카카오 BSA', 'SA', '카카오BSA_PC_0513', 10),
('카카오 BSA', 'SA', '카카오BSA_MO_0513', 10)
ON DUPLICATE KEY UPDATE priority = VALUES(priority), active = TRUE;

INSERT INTO rule_engine_column_alias_map (user_term, target_table, target_column, semantic_role, priority) VALUES
('캠페인', 'DA', '캠페인', 'dimension', 15),
('캠페인 이름', 'DA', '캠페인', 'dimension', 15),
('광고 그룹', 'DA', '광고 그룹', 'dimension', 10),
('광고 그룹', 'SA', '광고 그룹', 'dimension', 10),
('디바이스', 'DA', '디바이스', 'dimension', 10),
('디바이스', 'SA', '디바이스', 'dimension', 10),
('날짜', 'DA', '날짜', 'dimension', 10),
('날짜', 'SA', '날짜', 'dimension', 10),
('세션 캠페인', 'SA', '세션 캠페인', 'dimension', 10),
('세션 수동 검색어', 'SA', '세션 수동 검색어', 'dimension', 10),
('세션 기본 채널 그룹', 'SA', '세션 기본 채널 그룹', 'dimension', 10),
('자연검색', 'SA', '세션 기본 채널 그룹', 'dimension', 10),
('주요 이벤트', 'SA', '주요 이벤트', 'metric', 10),
('총 사용자', 'SA', '총 사용자', 'metric', 10),
('광고상품', 'SA', '광고상품', 'dimension', 10),
('PC/모바일', 'SA', 'PC/모바일', 'dimension', 10),
('전환 수', 'SA', '주요 이벤트', 'metric', 10),
('가입 완료 수', 'SA', '주요 이벤트', 'metric', 10)
ON DUPLICATE KEY UPDATE semantic_role = VALUES(semantic_role), priority = VALUES(priority), active = TRUE;

INSERT INTO rule_engine_metric_definition (
    metric_code, user_term, target_table, source_channel_scope, event_filter,
    expression_type, source_column, denominator_column, zero_fallback, business_definition, priority
) VALUES
('signup_completion_count_sa', '가입 완료 수', 'SA', JSON_ARRAY('GA_SA_데이터_0513', 'GA_비광고_세션_전환_0513'), JSON_OBJECT('column', '이벤트 이름', 'operator', 'in', 'values', JSON_ARRAY('가입신청서_작성_완료__pc_공통_', '가입신청서_작성_완료__mo_공통_', 'esim_가입신청서_작성_완료__pc_공통_', 'esim_가입신청서_작성_완료__mo_공통_')), 'event_count', '주요 이벤트', NULL, '0', '가입 완료 수는 가입신청서 작성 완료 이벤트 행의 주요 이벤트 합계로 계산한다.', 5),
('conversion_count_sa', '전환 수', 'SA', JSON_ARRAY('GA_SA_데이터_0513', 'GA_비광고_세션_전환_0513'), JSON_OBJECT('column', '이벤트 이름', 'operator', 'in', 'values', JSON_ARRAY('가입신청서_작성_완료__pc_공통_', '가입신청서_작성_완료__mo_공통_', 'esim_가입신청서_작성_완료__pc_공통_', 'esim_가입신청서_작성_완료__mo_공통_')), 'event_count', '주요 이벤트', NULL, '0', '전환 수는 가입 완료 이벤트 조건 기반 metric이다.', 5),
('signup_completion_count_da', '가입 완료 수', 'DA', JSON_ARRAY('GA'), JSON_OBJECT('column', '이벤트 이름', 'operator', 'in', 'values', JSON_ARRAY('가입신청서_작성_완료__pc_공통_', '가입신청서_작성_완료__mo_공통_', 'esim_가입신청서_작성_완료__pc_공통_', 'esim_가입신청서_작성_완료__mo_공통_')), 'event_count', '세션수', NULL, '0', 'DA의 GA 행에서는 가입 완료 이벤트 조건의 세션수 합계로 계산한다.', 5),
('conversion_count_da', '전환 수', 'DA', JSON_ARRAY('GA'), JSON_OBJECT('column', '이벤트 이름', 'operator', 'in', 'values', JSON_ARRAY('가입신청서_작성_완료__pc_공통_', '가입신청서_작성_완료__mo_공통_', 'esim_가입신청서_작성_완료__pc_공통_', 'esim_가입신청서_작성_완료__mo_공통_')), 'event_count', '세션수', NULL, '0', 'DA의 GA 행에서는 전환 이벤트 조건의 세션수 합계로 계산한다.', 5)
ON DUPLICATE KEY UPDATE
    source_channel_scope = VALUES(source_channel_scope),
    event_filter = VALUES(event_filter),
    expression_type = VALUES(expression_type),
    source_column = VALUES(source_column),
    denominator_column = VALUES(denominator_column),
    zero_fallback = VALUES(zero_fallback),
    business_definition = VALUES(business_definition),
    priority = VALUES(priority),
    active = TRUE;

INSERT INTO rule_engine_protected_column_policy (target_table, column_name, protection_level, reason) VALUES
('DA', 'row_id', 'block_update', 'row identity'),
('SA', 'row_id', 'block_update', 'row identity'),
('DA', 'source_row_hash', 'block_update', 'rollback identity'),
('SA', 'source_row_hash', 'block_update', 'rollback identity'),
('DA', 'import_batch_id', 'block_update', 'import lineage'),
('SA', 'import_batch_id', 'block_update', 'import lineage'),
('DA', 'source_channel', 'block_update', 'source provenance'),
('SA', 'source_channel', 'block_update', 'source provenance'),
('DA', '날짜', 'block_update', 'raw date provenance'),
('SA', '날짜', 'block_update', 'raw date provenance'),
('DA', '세션 소스/매체', 'block_update', 'raw attribution'),
('SA', '세션 소스/매체', 'block_update', 'raw attribution'),
('DA', '세션 캠페인', 'block_update', 'raw attribution'),
('SA', '세션 캠페인', 'block_update', 'raw attribution'),
('DA', '캠페인', 'block_update', 'raw campaign name'),
('SA', '캠페인', 'block_update', 'raw campaign name'),
('DA', '광고 그룹', 'block_update', 'raw ad group name'),
('SA', '광고 그룹', 'block_update', 'raw ad group name'),
('DA', '캠페인 ID', 'block_update', 'source id'),
('DA', '광고 그룹 ID', 'block_update', 'source id'),
('DA', '광고 소재 ID', 'block_update', 'source id')
ON DUPLICATE KEY UPDATE reason = VALUES(reason), active = TRUE;
