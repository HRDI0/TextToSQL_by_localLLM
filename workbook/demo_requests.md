# Demo Requests Workbook

This workbook keeps natural-language demo examples outside runtime workflow defaults.

The examples below are illustrative only. They must not be treated as fixed parser rules,
test expectations, source-channel values, campaign values, or customer-specific defaults.
Runtime parsing should rely on live schema metadata and user-provided request text.

## Example: Read Raw Search Ads

Selection:

```text
5월 검색광고 원본 데이터만 조회해줘.
```

Modification:

```text
매체명이 '검색매체A'이고 캠페인명이 'campaign_alpha' 또는 'campaign_beta'라면 광고상품 컬럼은 '검색광고 상품'으로 기입한다.
```

Notes:

- `검색매체A`, `campaign_alpha`, and `campaign_beta` are placeholder values.
- Replace media, campaign, customer, date, and source-channel terms with values from the target environment.
- Do not copy these values into parser prompts, workflow defaults, or semantic check rules.
