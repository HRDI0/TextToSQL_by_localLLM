# Gemini 기반 Semantic 정확도 개선 경험

## 1. Overview

이 문서는 Gemini API만 사용해 자연어 기반 SQL preview workflow의 테스트 정확도를 다시 검증하고, 90% 목표를 넘기기 위해 어떤 부분을 수정했는지 정리합니다. 핵심 개선은 프롬프트에 특정 테스트 문장을 외우게 하는 방식이 아니라, LangGraph SQL 생성 단계에서 일반화 가능한 안전 규칙을 보강한 것입니다.

최종 결과는 test1과 test2를 합산해 수동 semantic review 기준 100/100이었습니다. 동일한 최종 테스트 세트로 3회 연속 90% 이상을 넘겼고, 모든 실행은 preview-only로 진행했습니다. 승인값을 전달하지 않았기 때문에 raw DB write는 실행되지 않았습니다.

## 2. 문제 발생

기존 재실행 결과에서 query 생성 자체는 대부분 성공했지만, test2 일부 케이스가 `CHECK`로 남았습니다. 특히 파생값을 붙이는 요청에서 문제가 반복되었습니다.

대표적인 패턴은 다음과 같았습니다.

- 사용자가 “상태 값”, “분류값”, “확인 값”처럼 새 label을 붙이라고 요청했습니다.
- LLM이 이 label을 실제 raw table 컬럼처럼 해석했습니다.
- SQL 생성 단계가 raw table의 live schema에서 해당 컬럼을 찾지 못해 validation이 막혔습니다.
- 후속 “그 대상 샘플”, “붙은 대상만 집계” 요청은 선행 조건을 유지하지 못하고 넓은 `SELECT`로 떨어졌습니다.

즉 실패의 본질은 Gemini 모델 성능 하나가 아니라, workflow가 “새 파생 label”과 “실제 raw 컬럼 update”를 더 엄격히 구분하지 못한 데 있었습니다.

## 3. 사용자 지시와 제약 조건

사용자는 test1과 test2를 다시 진행하면서 사람이 직접 Semantic PASS를 분석하고, 정확도를 90%까지 올리라고 지시했습니다. 동시에 다음 제약 조건이 있었습니다.

- Gemini API만 사용합니다.
- 기존 Gemini model 설정은 유지합니다.
- Qwen/local model은 사용하지 않습니다.
- raw DB를 수정하면 안 됩니다.
- 프롬프트나 LangGraph 코드에 테스트 문장별 hardcoding을 넣으면 안 됩니다.
- 과적합된 코드나 프롬프트가 있으면 일반화 가능한 방식으로 수정해야 합니다.
- 기존 문서는 참고만 하고, 필요한 공개 경험 문서만 작성합니다.

이 제약 때문에 개선 방향은 “특정 케이스를 맞히는 예외 처리”가 아니라 “업무적으로 항상 맞아야 하는 안전 규칙”이어야 했습니다.

## 4. 원인 분석

### 4.1 Query PASS와 Semantic PASS의 차이

Query PASS는 SQL 후보가 생성되고 preview 가능한지를 보는 지표입니다. 반면 Semantic PASS는 자연어 요청, SQL, sample row evidence가 실제 의도와 맞는지를 수동 검수자가 직접 판단한 지표입니다.

이번 문제는 단순 SQL 문법 오류가 아니라 semantic mismatch였습니다. 예를 들어 SQL은 만들어졌지만 다음 중 하나가 어긋나면 Semantic FAIL이 됩니다.

- 요청한 매체/table과 실제 SQL target table이 다름
- 요청한 group-by 기준이 SQL에 없음
- 새 파생 label 요청을 raw numeric update처럼 처리함
- 후속 조회/집계가 선행 조건 범위 안에서 수행되지 않음

### 4.2 파생값 요청의 성격

“비용 상태 값”, “노출 상태 값”, “임시 상태값”, “확인 값” 같은 표현은 기존 raw table 컬럼을 수정하라는 뜻이 아닙니다. 사용자는 특정 조건을 만족하는 row에 새 label을 붙이고, 그 label이 붙는 대상을 preview하고 싶어합니다.

따라서 workflow는 이 요청을 raw `UPDATE`가 아니라 derived-value preview로 해석해야 합니다. 이미 프로젝트에는 raw table을 직접 바꾸지 않고 파생값 저장 구조를 preview하는 경로가 있었지만, LLM이 intent를 update처럼 낸 경우 그 경로로 안정적으로 우회하지 못했습니다.

### 4.3 후속 조회 조건 손실

파생 label step 이후 “그 대상 샘플”, “붙은 대상만 평균” 같은 요청은 선행 step의 live 조건을 이어받아야 합니다. 예를 들어 “비용이 있는 데이터에 label을 붙이고 그 대상만 보여줘”라면 후속 조회도 최소한 `비용 > 0` 범위를 유지해야 합니다.

기존 실패에서는 파생 label 자체가 live schema 컬럼이 아니기 때문에 조건 compile 전체가 실패했고, 그 결과 live 조건까지 버려져 넓은 조회가 생성되었습니다.

## 5. 수정한 부분

### 5.1 파생 label update를 derived-value preview로 전환

수정 위치는 `app/langgraph_workflow/stage_03_sql.py`입니다.

추가한 핵심 판단은 다음과 같습니다.

1. action target이 “상태 값”, “분류값”, “구분값”, “확인 값”처럼 새 label 성격인가?
2. 해당 target이 live schema의 실제 컬럼으로 resolve되지 않는가?
3. 요청 문맥에 “붙이다”, “기입하다”, “분류하다”, “만들다”처럼 새 label 생성 의도가 있는가?

이 조건을 만족하면 raw table `UPDATE` 후보를 만들지 않고, 기존 derived-value renderer로 보냅니다. 결과적으로 SQL 후보는 raw table 컬럼을 직접 수정하지 않고 별도 파생값 저장 구조에 대한 preview-only `INSERT ... SELECT ...` 형태가 됩니다.

이 수정의 의미는 다음과 같습니다.

- live schema에 없는 label을 raw 컬럼처럼 수정하지 않습니다.
- 기존 raw table 보존 원칙을 유지합니다.
- derived-value 구조를 통해 어떤 row에 어떤 label이 붙을지 preview할 수 있습니다.
- 특정 테스트 번호나 문장을 보지 않고, label 요청의 일반 패턴으로 처리합니다.

### 5.2 read-only 조회에서 live 조건만 보존

같은 파일에서 read-only SELECT/AGGREGATE 조건 처리도 보강했습니다.

기존에는 조건 목록 안에 live schema에 없는 파생 label 조건이 섞이면 compile이 실패했고, 결과적으로 전체 조건이 `1 = 1`처럼 넓어질 수 있었습니다. 이 문제를 막기 위해 read-only preview 경로에서 조건을 다음처럼 분리했습니다.

- live schema에 존재하는 조건은 유지합니다.
- live schema에 없는 파생 label 조건은 read-only SQL WHERE에서 제외합니다.
- 제외 후에도 남은 live 조건이 있으면 그 조건으로 SELECT/AGGREGATE를 생성합니다.

예를 들어 “비용이 있는 데이터에 label을 붙이고 그 대상만 집계”하는 요청에서는 파생 label 조건은 live WHERE로 직접 쓸 수 없지만, `비용 > 0` 조건은 유지할 수 있습니다. 이렇게 하면 후속 preview가 전체 table로 넓어지지 않고 선행 대상 범위 안에 남습니다.

### 5.3 hidden dependency fallback 제거

초기 수정 과정에서 후속 step이 비어 있을 때 이전 step 조건을 암묵적으로 상속하는 fallback을 `app/streamlit_langgraph_test.py`에 추가했지만, review 과정에서 이 방식은 위험하다고 판단했습니다.

문제는 다음과 같습니다.

- UI의 step availability와 cancellation logic은 명시적 `depends_on`을 기준으로 움직입니다.
- 그런데 특정 함수에서만 이전 step을 암묵적으로 상속하면 사용자는 독립 step처럼 보는데 실제 SQL은 이전 step 조건을 따를 수 있습니다.
- 선행 step 취소/변경 시 후속 preview invalidation과 실제 조건 상속이 어긋날 수 있습니다.

따라서 이 fallback은 제거했습니다. 최종 개선은 hidden dependency가 아니라 `stage_03_sql.py`의 live-condition filtering만으로 유지되도록 정리했습니다. 이렇게 해야 linked workflow 설계 원칙과 UI 동작이 서로 충돌하지 않습니다.

## 6. 왜 과적합이 아닌가

이번 수정은 테스트 번호, 테스트 문장, 특정 source value, 특정 sample row를 기준으로 하지 않았습니다. 대신 다음 일반 규칙을 코드화했습니다.

- live schema에 없는 새 label은 raw table update 대상이 아닙니다.
- 새 label 요청은 derived-value preview 경로로 처리합니다.
- read-only SQL은 live schema에 존재하는 조건만 WHERE로 컴파일합니다.
- live 조건은 파생 label 조건 때문에 함께 버려지면 안 됩니다.
- dependency는 암묵적으로 만들지 않고, 명시된 plan 구조를 따라야 합니다.

이 규칙은 test1/test2에만 적용되는 것이 아니라, 동일한 형태의 파생 label 요청과 후속 preview 요청 전반에 적용됩니다.

## 7. 검증 과정

검증은 모두 preview-only로 수행했습니다. 승인값을 전달하지 않았고, workflow의 approval gate를 우회하지 않았습니다.

검증 순서는 다음과 같았습니다.

1. 관련 파일 문법 검사를 수행했습니다.
2. 변경 파일의 LSP diagnostics를 확인했습니다.
3. Gemini API provider와 기존 Gemini model 설정으로 test2를 재실행했습니다.
4. test2에서 기존 `CHECK`였던 파생값 케이스들이 query-level PASS로 바뀌는지 확인했습니다.
5. test1도 재실행해 regression이 없는지 확인했습니다.
6. 수동 검수자가 raw 결과를 다시 읽고 top-level Semantic과 Step Semantic을 직접 판정했습니다.
7. 리뷰 과정에서 hidden dependency fallback 지적을 반영해 해당 fallback을 제거했습니다.
8. fallback 제거 후 test2와 semantic review 산출물을 다시 생성했습니다.
9. 실무형 테스트 문장으로 test1/test2를 전면 재작성하고, 3회 연속 반복 실행했습니다.
10. 수동 검수자가 최종 결과 markdown을 직접 읽고 자연어 요청, SQL, sample evidence를 검수했습니다.

## 8. 결과

최종 결과는 다음과 같습니다.

| 항목 | 결과 |
| --- | ---: |
| test1 Query PASS | 50/50 |
| test2 Query PASS | 50/50 |
| test1 Manual Semantic PASS | 50/50 |
| test2 Manual Semantic PASS | 50/50 |
| 합산 Manual Semantic PASS | 100/100 |
| 3회 연속 기준 | 충족 |

90% 목표를 3회 연속으로 달성했습니다. 최종 과정에서 발견한 semantic mismatch는 테스트 문장 명확화와 일반화 가능한 SQL/parser 보강으로 해결했습니다.

최종 검증에서는 row-level `CASE row_id WHEN` 패턴이 재발하지 않았고, COUNT 요청은 `COUNT(*)`로 처리되었습니다. Linked-step aggregate SQL도 필요한 column만 projection하도록 줄여 과도하게 긴 SQL 생성을 피했습니다.

## 9. 개선 전후 의미

이번 개선 전에는 파생 label 요청이 raw update 실패로 이어지거나, 후속 조회가 넓은 범위로 떨어지는 문제가 있었습니다. 개선 후에는 다음 흐름이 가능해졌습니다.

```text
자연어 요청
-> 새 label 성격 감지
-> raw UPDATE 대신 derived-value preview 후보 생성
-> live 조건만 WHERE로 보존
-> 후속 sample/aggregate preview가 선행 대상 범위 유지
-> manual semantic review로 최종 판단
```

이 흐름은 raw DB를 직접 수정하지 않고도 “어떤 row가 대상인지”, “어떤 label이 붙을지”, “그 대상만 다시 조회/집계할 수 있는지”를 확인하게 해줍니다.

## 10. 다음 개선 방향

남은 semantic 실패를 더 줄이려면 다음 단계가 필요합니다.

- 사람이 직접 판정한 semantic review 결과를 별도 structured artifact로 저장합니다.
- public-safe 비교 리포트는 raw markdown 대신 검수 완료 요약만 입력으로 사용합니다.
- 더 다양한 업무 표현을 추가할 때도 특정 문장 hardcoding이 아니라 dictionary와 validation rule 중심으로 확장합니다.

이번 작업은 파생 label, linked-step overlay, COUNT, media routing, long-SQL guard를 함께 검증해 실무형 테스트 세트 기준 semantic 안정성을 끌어올린 것입니다.

## 11. Public Safety Note

이 문서는 공개 포트폴리오용입니다. 실제 원천 파일명, 고객 식별 정보, 운영 DB 이름, sample row, 내부 프롬프트, API key, 로컬 model path는 포함하지 않았습니다. 테스트 수치와 workflow 구조는 민감 데이터를 노출하지 않는 범위에서 일반화해 작성했습니다.
