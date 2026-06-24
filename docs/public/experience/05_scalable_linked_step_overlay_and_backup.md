# Scalable Linked-Step Overlay와 Backup Gate 개선 경험

## 1. Overview

이 문서는 자연어 기반 SQL 검토 워크플로우에서 linked-step preview가 길고 비효율적인 SQL을 만들던 문제를 분석하고, DB-backed delta overlay와 backup gate로 개선한 과정을 정리합니다. 핵심은 “선행 보정 결과를 후속 계산에 반영하되, raw table은 preview 단계에서 수정하지 않고, 최종 승인 시에는 반드시 backup 이후에만 수정한다”는 원칙을 지키는 것이었습니다.

이번 문서는 공개용 경험 기록입니다. 실제 원천 데이터, 고객 식별 정보, 운영 DB 이름, 샘플 row, 내부 프롬프트, 로컬 모델 경로는 포함하지 않습니다.

## 2. 문제 발생

linked request에서는 사용자가 한 문장 안에서 “먼저 특정 값을 보정하고, 그 결과를 기준으로 다시 집계해 달라”는 요청을 할 수 있습니다. 이때 첫 번째 UPDATE preview는 raw table을 직접 수정하지 않아야 하지만, 두 번째 SELECT나 aggregate preview는 사용자가 승인한 선행 보정 결과를 반영해야 합니다.

초기 개선에서는 선행 step의 preview delta를 후속 SELECT에 반영하기 위해 row별 `CASE row_id WHEN ... THEN ...` 형태의 SQL overlay를 만들었습니다. 이 방식은 기능적으로는 선행 보정 값을 반영할 수 있었지만, 영향 row가 많아지면 SQL 문자열이 매우 커졌습니다. 실제 점검 과정에서 수만 개의 `WHEN` 절이 생길 수 있다는 점이 드러났고, 이는 운영 가능한 방식이 아니라고 판단했습니다.

또 하나의 문제는 final approval 경로였습니다. preview와 테스트에서는 raw table을 수정하지 않아야 하지만, 사용자가 최종 승인한 UPDATE는 raw table에 실제 반영되어야 합니다. 이때 rollback이나 감사 목적의 backup 없이 raw table을 수정하면 데이터 안전성을 설명하기 어렵습니다.

## 3. 문제 분석

문제는 크게 세 층으로 나누어 볼 수 있었습니다.

첫째, overlay 표현 방식이 row 수에 비례했습니다. `CASE row_id WHEN ...` 방식은 변경 row마다 SQL 조각이 추가됩니다. row가 수십 건일 때는 눈에 띄지 않지만, 수천 또는 수만 건으로 늘어나면 SQL 크기, 파라미터 수, 렌더링 로그 크기, DB 파서 부담이 모두 커집니다.

둘째, preview state와 DB state의 책임이 섞여 있었습니다. linked-step preview는 화면에는 일부 sample만 보여주지만, 후속 step 계산에는 전체 영향 row의 hypothetical delta가 필요합니다. 이 전체 delta를 UI session이나 SQL 문자열에 직접 싣는 것은 확장성이 낮습니다.

셋째, approval과 execution의 의미가 분리되어야 했습니다. 중간 step 승인은 “후속 preview에 이 delta를 반영해도 된다”는 뜻이지, raw table을 즉시 수정한다는 뜻이 아닙니다. 반면 최종 승인은 실제 UPDATE를 허용할 수 있지만, 이때는 fingerprint match와 backup coverage가 모두 확인되어야 합니다.

## 4. 해결방안 조사

조사 과정에서는 세 가지 방향을 비교했습니다.

첫 번째 대안은 기존 row-level `CASE`를 유지하되 조건을 더 줄이는 방식이었습니다. 하지만 row 수에 비례한다는 근본 문제가 남기 때문에 제외했습니다.

두 번째 대안은 preview delta를 DB table에 저장하고, 후속 SELECT가 raw table과 delta table을 join해 effective relation을 만드는 방식이었습니다. 이 방식은 SQL 크기가 row 수가 아니라 changed column 수와 dependency step 수에 주로 비례합니다. 또한 preview delta의 승인/취소 상태, 만료 시간, linked plan id를 DB에서 관리할 수 있어 workflow 상태와 잘 맞았습니다.

세 번째 대안은 Python preview layer에서만 sample row를 patch하는 방식이었습니다. 이 방식은 화면 SQL을 가장 짧게 만들 수 있지만, 표시되는 SQL과 표시되는 sample row의 의미가 달라질 수 있습니다. 따라서 핵심 계산 SQL에는 적합하지 않고, UI 표시 최적화 후보로만 남겼습니다.

최종 선택은 두 번째 방향이었습니다. `rule_engine_delta_item`을 linked-step preview delta의 저장소로 사용하고, 후속 SELECT/aggregate는 approved delta만 join하도록 설계했습니다.

## 5. 해결 과정

### 5.1 Delta overlay 저장소 도입

UPDATE preview가 만들어질 때 전체 영향 row의 `before`, `after`, `delta`를 `rule_engine_delta_item`에 저장하도록 했습니다. 화면과 output에는 sample delta만 남기고, 전체 delta는 DB-backed overlay source로 분리했습니다.

이 구조로 바꾸면서 linked plan id, step key, step order, target table, source row id, delta status를 overlay lookup의 기준으로 사용했습니다. 후속 step은 승인된 선행 delta만 읽으며, pending 또는 cancelled delta는 effective relation에 반영하지 않습니다.

### 5.2 Row-level CASE 제거

후속 SELECT/aggregate SQL은 더 이상 `CASE row_id WHEN ...`를 생성하지 않습니다. 대신 raw table을 기준으로 `rule_engine_delta_item`을 join하고, 특정 column에 approved delta가 있으면 `after_json` 값을 읽고, 없으면 raw column 값을 사용합니다.

이때 중요한 점은 dependency scope입니다. 단순히 같은 linked plan의 “이전 approved step”을 모두 반영하면 독립 step의 delta가 섞일 수 있습니다. 그래서 overlay context에 dependency ancestor step key를 포함하고, SQL lookup에서도 `step_key`를 필터링하도록 보강했습니다.

### 5.3 Dependent UPDATE 안전 차단

SELECT나 aggregate는 effective relation 위에서 preview할 수 있지만, UPDATE는 raw table을 실제로 수정하는 후보입니다. 선행 delta가 바꾼 column을 predicate로 사용하는 dependent UPDATE를 안전하게 raw UPDATE로 변환하려면 별도 write path가 필요합니다.

따라서 이번 개선에서는 그런 dependent UPDATE를 무리하게 실행하지 않고 validation 단계에서 차단했습니다. 이는 기능을 덜 제공하더라도 잘못된 raw update를 막는 쪽이 더 안전하다는 판단이었습니다.

### 5.4 Backup-protected final execution

최종 승인된 UPDATE는 raw table을 수정할 수 있습니다. 다만 실행 전에 대상 row 전체를 `rule_engine_raw_update_backup`에 먼저 저장하도록 했습니다.

초기 구현은 backup table이 없으면 skip하고 UPDATE를 계속할 수 있는 여지가 있었지만, 검토 과정에서 이 동작은 안전 원칙에 맞지 않다고 판단했습니다. 그래서 backup table이 없거나, row id가 없거나, 대상 row 전체가 backup scope에 저장되지 않으면 UPDATE 전에 예외를 발생시키도록 fail-closed 방식으로 수정했습니다.

### 5.5 Test runner와 UI 연결

Streamlit UI는 linked request마다 linked plan을 만들고, 각 step preview가 승인되면 delta status를 approved로 바꿉니다. 후속 step preview는 현재 step의 dependency ancestor에 해당하는 approved delta만 반영합니다.

Test runner도 같은 구조를 따르도록 조정했습니다. 각 test case마다 독립 linked plan을 만들고, PASS preview step만 approved delta로 표시합니다. 그러나 test runner는 final raw approval path를 호출하지 않으므로 preview-only 테스트 중 raw table은 수정되지 않습니다.

## 6. 검증 과정

검증은 기능 검증과 안전 검증을 나누어 진행했습니다.

기능 검증에서는 linked-step aggregate가 approved delta를 반영하는지 확인했습니다. SQL 결과에는 `rule_engine_delta_item` join과 dependency step key filter가 포함되어야 하고, row 수만큼 늘어나는 `CASE row_id WHEN` 패턴은 없어야 했습니다.

안전 검증에서는 preview-only 테스트 후 backup table row 수가 늘어나지 않는지 확인했습니다. 이는 preview/test가 raw UPDATE path를 호출하지 않았다는 간접 증거입니다. 반대로 final UPDATE path에서는 backup table이 없거나 coverage가 부족하면 실행 전 실패해야 합니다.

검증 결과는 다음 기준을 만족했습니다.

- Gemini native model 기준 테스트 세트 2종이 모두 query PASS를 기록했습니다.
- 결과 markdown에서 `CHECK` 또는 `ERROR` 상태가 나오지 않았습니다.
- row-level `CASE row_id WHEN` 패턴이 나오지 않았습니다.
- `rule_engine_delta_item` join과 dependency step key filter가 확인되었습니다.
- preview-only 테스트 후 raw backup table row 수가 증가하지 않았습니다.
- 정적 검증에서 workflow core 파일은 오류 없이 통과했습니다.

## 7. 결과

이번 개선으로 linked-step preview는 다음 성격을 갖게 되었습니다.

- 선행 UPDATE preview는 raw table을 수정하지 않고 delta item으로 저장됩니다.
- 후속 SELECT/aggregate는 approved dependency delta를 반영해 계산합니다.
- overlay SQL은 row 수에 비례하지 않습니다.
- 독립 step의 delta가 후속 step에 섞이지 않도록 dependency step key를 사용합니다.
- 최종 UPDATE는 backup coverage가 확인되어야만 실행됩니다.
- overlay-aware write path가 없는 dependent UPDATE는 validation에서 차단됩니다.

이 결과는 “연계 질문을 더 잘 해석한다”는 수준을 넘어, preview-first workflow의 안전성과 확장성을 함께 개선한 작업이었습니다.

## 8. 남은 개선 방향

아직 개선 여지는 있습니다.

첫째, overlay SQL은 row 수에는 비례하지 않지만 table column 수에는 영향을 받습니다. 현재는 effective relation을 만들 때 raw column을 많이 펼칠 수 있으므로, query에 필요한 column만 projection하도록 줄이는 최적화가 다음 후보입니다.

둘째, dependent UPDATE를 안전하게 지원하려면 effective relation에서 대상 row id를 먼저 확정하고, 그 row id 집합에 대해 raw UPDATE와 backup을 수행하는 별도 write path가 필요합니다. 이번 작업에서는 잘못된 UPDATE를 막기 위해 차단을 선택했습니다.

셋째, semantic review 결과를 자동 집계하려면 preview report와 사람이 검토한 semantic judgment를 분리 저장하는 흐름이 더 명확해져야 합니다.

## 9. 배운 점

이번 문제의 핵심은 기능이 “맞아 보이는 것”과 운영 가능한 구조가 다르다는 점이었습니다. row-level `CASE`는 작은 샘플에서는 정상처럼 보였지만, row 수가 늘어나면 즉시 한계가 드러났습니다.

또한 preview workflow에서는 “승인”이라는 단어가 항상 실행을 의미하지 않습니다. 중간 step 승인은 후속 preview에 사용할 수 있는 가상 상태를 확정하는 것이고, 최종 승인은 raw table mutation을 허용하는 것입니다. 이 두 의미를 분리해야 안전한 UX와 안전한 DB 실행 경로를 동시에 유지할 수 있습니다.

마지막으로, 안전하지 않은 기능을 억지로 지원하는 것보다 validation에서 명시적으로 차단하는 편이 낫습니다. 특히 raw data를 수정하는 경로에서는 backup과 fingerprint gate가 기능보다 우선해야 합니다.

## 10. Public Safety Note

이 문서는 공개 포트폴리오용입니다. 실제 원천 데이터, 고객 식별 정보, 운영 DB 이름, 파일명, 샘플 row, 내부 프롬프트, 로컬 모델 경로는 포함하지 않았습니다. table명과 workflow component명은 설계를 설명하기 위한 공개 가능한 수준에서만 사용했습니다.
