# 연계 질문 성능 개선을 위한 DB/LangGraph 설계 개선 경험

## 1. Overview

이 문서는 연계 질문 테스트 결과가 낮게 나온 원인을 분석하고, 개선 우선순위에 따라 DB 설계와 LangGraph 설계를 먼저 보강한 작업을 정리합니다. 핵심 개선 방향은 “연계 질문을 인식하는 것”에서 “연계 step을 실행 단위로 보존하는 것”으로 바꾸는 것입니다.

이번 개선은 실제 SQL 실행 경로를 우회하지 않습니다. 기존 preview-first, approval-gated 원칙을 유지하면서, linked step plan을 state와 output에 first-class artifact로 노출하고, DB에는 linked plan과 step별 검증/preview/승인 상태를 기록할 수 있는 스키마를 추가했습니다.

## 2. 문제 발생

재평가 결과 Query PASS는 거의 높았지만 Linked Coherence PASS는 상대적으로 낮았습니다. 즉 SQL 후보 생성은 대체로 가능했지만, “먼저 보정하고 그 결과를 기준으로 다시 계산”, “서로 다른 작업을 분리해서 보여주기”, “선행 step이 취소되면 후속 step도 재검토” 같은 workflow 의미가 안정적으로 유지되지 않았습니다.

실패 원인은 크게 두 가지였습니다.

첫째, step semantic 실패가 linked coherence 실패로 전파되었습니다. 집계/조회 샘플이 없거나, after 값이 0으로 확인되지 않거나, count 요청인데 COUNT가 없는 경우가 반복되었습니다.

둘째, 선행 보정/파생 이후 후속 조회가 실제로 선행 결과에 연결되지 않는 문제가 있었습니다. 자연어는 순서를 요구하지만 backend는 하나의 SQL candidate 중심으로 수렴했기 때문에 step 간 dependency를 실행 단위로 보존하기 어려웠습니다.

## 3. 사용자 지시와 개선 목표

사용자는 연계 질문 테스트 결과가 좋지 않으니 개선 방법을 조사하라고 지시했습니다. 이후 지금까지의 문제 해결 과정과 개선 작업을 포트폴리오 문서로 작성하고, 개선 우선순위에 따라 실제 작업도 진행하라고 요청했습니다.

개선 목표는 다음처럼 잡았습니다.

- DB 설계는 linked plan과 step 단위 상태를 저장할 수 있어야 합니다.
- LangGraph 설계는 `workflow_steps`를 단순 metadata가 아니라 plan artifact로 보존해야 합니다.
- 기존 preview/approval/fingerprint gate는 유지해야 합니다.
- raw DB를 직접 수정하거나 SQL 실행 안전장치를 우회하면 안 됩니다.
- public docs에는 실제 운영 데이터나 내부 프롬프트를 노출하지 않아야 합니다.

## 4. 원인 분석

### 4.1 LangGraph 구조 분석

기존 parser는 multi-step 요청을 `condition_groups`로 나누고 `workflow_steps`를 생성할 수 있었습니다. 하지만 core graph는 여전히 다음과 같은 단일 path였습니다.

```text
parse -> rule lookup -> one SQL candidate -> one validation -> one preview -> one approval
```

이 구조에서는 step이 여러 개 있어도 최종적으로 하나의 `sql_candidate`와 하나의 `change_preview_json`으로 수렴합니다. 따라서 step별 validation, step별 preview, step별 approval, step별 dependency 상태를 backend artifact로 충분히 보존하기 어렵습니다.

### 4.2 DB 설계 분석

기존 DB 설계에는 실행 로그와 파생값 저장 구조가 있었지만, linked plan 자체를 저장하는 구조는 부족했습니다. run-level execution log는 전체 요청의 SQL과 preview 요약을 남기기에는 충분하지만, 다음 질문에 답하기 어렵습니다.

- 어떤 step이 선행 step이었는가
- 어떤 step이 dependent였는가
- 특정 step의 preview fingerprint는 무엇인가
- 어떤 step만 승인되거나 거절되었는가
- 후속 step은 어떤 선행 step 결과를 전제로 했는가

연계 질문을 운영 수준에서 개선하려면 run-level log뿐 아니라 step-level plan과 상태를 저장할 수 있어야 합니다.

## 5. 개선 우선순위

가장 먼저 해야 할 일은 모델 prompt를 더 길게 만드는 것이 아니었습니다. 이미 parser와 UI에는 step 개념이 있었기 때문에, 우선 backend state와 DB 설계를 step-aware하게 만드는 것이 더 큰 효과를 낼 수 있다고 판단했습니다.

우선순위는 다음과 같이 정했습니다.

1. Linked step plan을 LangGraph state와 output에 first-class artifact로 보존합니다.
2. Linked plan과 step 상태를 저장할 DB 스키마를 추가합니다.
3. 이후 단계에서 step별 SQL/validation/preview/result를 분리합니다.
4. Dependency-aware validator와 repair loop를 추가합니다.
5. Count, aggregate, after-zero sample evidence 같은 semantic guard를 보강합니다.

이번 작업에서는 1번과 2번을 먼저 구현했습니다. 이는 기존 실행 흐름을 깨지 않으면서 다음 단계의 기반을 만드는 안전한 개선입니다.

## 6. LangGraph 설계 개선

### 6.1 State 확장

기존 state에는 `workflow_steps`가 있었지만 linked plan의 validation 결과나 step 결과를 담는 명시적 필드가 부족했습니다. 그래서 state에 다음 필드를 추가했습니다.

- `linked_step_plan`
- `linked_step_validation`
- `linked_step_results`

이 필드는 기존 `workflow_steps`를 대체하지 않습니다. 기존 UI와 output 호환성을 유지하면서, 더 구조화된 step plan을 별도 artifact로 추가했습니다.

### 6.2 Linked Step Plan 생성

parser 단계에서 `condition_groups`를 기반으로 linked step plan을 생성하도록 했습니다. 각 step은 다음 정보를 가집니다.

- `step_id`, `group_id`, `step_order`
- `intent_type`
- `dependency`, `depends_on`
- `selection_scope`
- `conditions`, `actions`, `group_by`, `metrics`, `derived_column`
- expected artifacts: SQL candidate, validation result, preview JSON, user confirmation
- execution gate: preview-first approval required

이 설계의 목적은 단순히 step을 화면에 보여주는 것이 아니라, 이후 각 step을 독립적인 검증/preview 단위로 승격할 수 있도록 만드는 것입니다.

### 6.3 Linked Step Validation 추가

plan 생성 직후 dependency 구조를 검증하는 validation도 추가했습니다. 이 validation은 다음을 확인합니다.

- step id가 비어 있지 않은가
- 자기 자신을 dependency로 참조하지 않는가
- 존재하지 않는 dependency를 참조하지 않는가
- 후행 step을 선행 dependency처럼 참조하지 않는가
- dependent step이 `depends_on` 없이 선언되지 않았는가

이 단계는 SQL을 실행하지 않습니다. 대신 linked workflow의 구조적 오류를 SQL 후보 생성 전에 드러내는 역할을 합니다.

### 6.4 Output 확장

최종 output JSON에도 `linked_step_plan`, `linked_step_validation`, `linked_step_results`를 포함했습니다. 이렇게 하면 UI나 후속 평가기가 동일한 state artifact를 기준으로 linked workflow를 확인할 수 있습니다.

## 7. DB 설계 개선

DB 설계는 `005_linked_step_plan.sql` migration으로 분리했습니다. 기존 execution log를 직접 변경하지 않고, linked plan 전용 table을 추가해 위험을 낮췄습니다. 이 단계의 DB 설계는 step별 preview payload 전체를 저장하는 완성형 실행 로그가 아니라, step별 상태와 fingerprint, plan/result JSON을 남길 수 있는 기반 스키마입니다.

### 7.1 Linked Plan Table

`rule_engine_linked_plan`은 요청 단위의 linked plan metadata를 저장하기 위한 table입니다.

주요 컬럼은 다음과 같습니다.

- `request_fingerprint`
- `step_count`
- `dependent_step_count`
- `validation_status`
- `validation_errors`
- `plan_status`

이 table은 “하나의 자연어 요청이 어떤 linked plan으로 해석되었는가”를 기록하기 위한 기준점입니다.

### 7.2 Linked Plan Step Table

`rule_engine_linked_plan_step`은 step 단위의 검증, preview fingerprint, 승인, 실행 상태를 저장하기 위한 table입니다.

주요 컬럼은 다음과 같습니다.

- `linked_plan_id`
- `step_order`
- `step_key`
- `intent_type`
- `dependency_type`
- `depends_on_json`
- `target_table`
- `step_status`
- `sql_fingerprint`
- `validation_status`
- `preview_fingerprint`
- `approval_status`
- `execution_status`
- `plan_json`
- `result_json`

이 설계는 다음 단계의 구현을 준비합니다. 나중에 step별 SQL 후보, validation 결과, preview fingerprint, approval decision을 각각 연결할 수 있습니다. 실제 preview row payload를 장기 보관해야 한다면 별도 detail table이나 `result_json` 저장 정책을 추가로 정해야 합니다.

## 8. 결과

이번 개선으로 연계 질문 처리를 위한 기반이 다음처럼 바뀌었습니다.

- parser가 만든 step 구조가 `linked_step_plan`으로 명시화되었습니다.
- dependency 구조가 `linked_step_validation`으로 검증됩니다.
- final output이 linked plan과 validation 결과를 포함합니다.
- DB에는 linked plan과 step 상태를 저장할 수 있는 migration이 추가되었습니다.
- 기존 단일 SQL candidate, preview, approval gate는 그대로 유지되어 안전장치를 우회하지 않습니다.

즉 이번 개선은 “바로 모든 step을 실행하는 기능”이 아니라, step별 실행/preview/승인으로 확장하기 위한 설계 기반 작업입니다. 이 접근은 운영 안전성을 유지하면서 다음 단계 개선을 가능하게 합니다.

## 9. 다음 개선 방향

다음 단계에서는 linked step plan을 실제 실행 단위로 연결해야 합니다.

우선 `active_step_id` 또는 step iterator를 도입해 각 step만 필터링한 IR로 SQL candidate를 만들 수 있어야 합니다. 이후 stage 03은 step별 SQL 후보와 validation 결과를 생성하고, stage 04는 step별 preview JSON과 approval fingerprint를 만들어야 합니다.

그 다음에는 dependent step이 선행 step의 preview/result를 입력으로 받아 후속 집계나 조회를 계산하도록 개선해야 합니다. 이 단계까지 가면 Linked Coherence PASS가 단순 평가 지표가 아니라 workflow 자체의 품질 게이트가 됩니다.

## 10. Public Safety Note

이 문서는 공개 포트폴리오용입니다. 실제 원천 데이터, 운영 DB 이름, 고객 식별 정보, source value, 샘플 row, 내부 프롬프트는 포함하지 않았습니다. DB table명은 workflow 설계를 설명하기 위한 공개 가능한 수준의 추상화로만 사용했습니다.
