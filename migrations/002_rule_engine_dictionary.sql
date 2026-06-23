SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS rule_engine_source_channel_map (
    mapping_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_term VARCHAR(255) NOT NULL,
    target_table VARCHAR(10) NOT NULL,
    source_channel VARCHAR(255) NOT NULL,
    priority INT NOT NULL DEFAULT 100,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (mapping_id),
    UNIQUE KEY uk_rule_engine_source_channel_map (user_term, target_table, source_channel),
    INDEX idx_rule_engine_source_channel_term (user_term),
    INDEX idx_rule_engine_source_channel_target (target_table, source_channel)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rule_engine_column_alias_map (
    alias_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_term VARCHAR(255) NOT NULL,
    target_table VARCHAR(10) NOT NULL,
    target_column VARCHAR(255) NOT NULL,
    semantic_role VARCHAR(100) NULL,
    priority INT NOT NULL DEFAULT 100,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (alias_id),
    UNIQUE KEY uk_rule_engine_column_alias_map (user_term, target_table, target_column),
    INDEX idx_rule_engine_column_alias_term (user_term),
    INDEX idx_rule_engine_column_alias_target (target_table, target_column)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rule_engine_metric_definition (
    metric_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    metric_code VARCHAR(100) NOT NULL,
    user_term VARCHAR(255) NOT NULL,
    target_table VARCHAR(10) NOT NULL DEFAULT '',
    expression_type VARCHAR(50) NOT NULL,
    source_column VARCHAR(255) NOT NULL,
    denominator_column VARCHAR(255) NULL,
    zero_fallback VARCHAR(50) NULL,
    priority INT NOT NULL DEFAULT 100,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (metric_id),
    UNIQUE KEY uk_rule_engine_metric_definition (metric_code, user_term, target_table),
    INDEX idx_rule_engine_metric_user_term (user_term),
    INDEX idx_rule_engine_metric_target (target_table)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO rule_engine_source_channel_map (user_term, target_table, source_channel, priority) VALUES
('네이버 검색광고', 'SA', 'naver_ksa_0513_columns', 10),
('네이버 검색광고', 'SA', '네이버NSA_0513', 10),
('네이버 SA', 'SA', 'naver_ksa_0513_columns', 20),
('네이버 SA', 'SA', '네이버NSA_0513', 20),
('네이버 브랜드검색', 'SA', '네이버BSA_0513', 10),
('네이버 브랜드검색', 'SA', '네이버BSA_소재요소_0513', 10),
('구글 검색광고', 'SA', '구글_0513', 10),
('구글 검색광고', 'SA', 'GA_SA_데이터_0513', 10),
('메타 광고', 'DA', '메타', 10),
('메타', 'DA', '메타', 20),
('페이스북 광고', 'DA', '메타', 10),
('카카오 디스플레이 광고', 'DA', '카카오', 10),
('카카오', 'DA', '카카오', 20),
('GDN 광고', 'DA', 'GDN', 10),
('GDN', 'DA', 'GDN', 20),
('GFA 광고', 'DA', 'GFA', 10),
('GFA', 'DA', 'GFA', 20),
('비광고 데이터', 'SA', 'GA_비광고_세션_전환_0513', 20),
('비광고 데이터', 'SA', 'GA_비광고_총사용자_0513', 20),
('KT샵 데이터', 'SA', 'GA_KT샵_데이터_0513', 20)
ON DUPLICATE KEY UPDATE priority = VALUES(priority), active = TRUE;

INSERT INTO rule_engine_column_alias_map (user_term, target_table, target_column, semantic_role, priority) VALUES
('캠페인 이름', 'SA', '캠페인', 'dimension', 10),
('캠페인 이름', 'DA', '세션 캠페인', 'dimension', 20),
('광고그룹 이름', 'SA', '광고 그룹', 'dimension', 10),
('키워드', 'DA', '세션 수동 검색어', 'dimension', 10),
('소재명', 'DA', '광고 소재', 'dimension', 10),
('소재명', 'DA', '세션 수동 광고 콘텐츠', 'dimension', 20),
('노출', 'SA', '노출수', 'metric', 20),
('노출', 'DA', '노출수', 'metric', 20),
('노출 수', 'SA', '노출수', 'metric', 10),
('노출 수', 'DA', '노출수', 'metric', 10),
('클릭', 'SA', '클릭수', 'metric', 20),
('클릭', 'DA', '클릭수', 'metric', 20),
('클릭 수', 'SA', '클릭수', 'metric', 10),
('클릭 수', 'DA', '클릭수', 'metric', 10),
('광고비', 'SA', '비용', 'metric', 10),
('광고비', 'DA', '비용', 'metric', 10),
('세션 수', 'SA', '세션수', 'metric', 10),
('세션 수', 'DA', '세션수', 'metric', 10),
('전환 수', 'SA', '세션수', 'metric', 20),
('전환 수', 'DA', '세션수', 'metric', 20),
('PC/모바일', 'SA', '디바이스', 'dimension', 10)
ON DUPLICATE KEY UPDATE semantic_role = VALUES(semantic_role), priority = VALUES(priority), active = TRUE;

INSERT INTO rule_engine_metric_definition (metric_code, user_term, target_table, expression_type, source_column, denominator_column, zero_fallback, priority) VALUES
('impressions_sum', '노출 수 합계', '', 'sum', '노출수', NULL, NULL, 10),
('clicks_sum', '클릭 수 합계', '', 'sum', '클릭수', NULL, NULL, 10),
('cost_sum', '광고비 합계', '', 'sum', '비용', NULL, NULL, 10),
('clicks_avg', '평균 클릭 수', '', 'avg', '클릭수', NULL, NULL, 10),
('cost_avg', '평균 광고비', '', 'avg', '비용', NULL, NULL, 10),
('sessions_avg', '평균 세션 수', '', 'avg', '세션수', NULL, NULL, 10),
('conversion_rate', '전환율', '', 'conversion_rate', '세션수', '클릭수', '0', 10),
('ctr', '클릭률', '', 'ctr', '클릭수', '노출수', '0', 10),
('cost_per_conversion', '전환당 비용', '', 'cost_per_conversion', '비용', '세션수', NULL, 10),
('cost_per_conversion', '전환당 광고비', '', 'cost_per_conversion', '비용', '세션수', NULL, 10)
ON DUPLICATE KEY UPDATE expression_type = VALUES(expression_type), source_column = VALUES(source_column), denominator_column = VALUES(denominator_column), zero_fallback = VALUES(zero_fallback), priority = VALUES(priority), active = TRUE;
