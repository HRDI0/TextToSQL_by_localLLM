# LangGraph Workflow Architecture

This document summarizes the current guarded LangGraph workflow at a public-safe level. It describes stage responsibilities, safety gates, linked-step preview behavior, and the runtime paths without publishing private prompts, source values, row samples, credentials, or local model paths.

Diagram: [workflow_architecture.svg](workflow_architecture.svg)

## Runtime Paths

The same compiled graph is used by both the local Streamlit review UI and the markdown test runner.

- Streamlit starts a review run from user text and optional approval state.
- The test runner calls the graph with `approved=False`, so it can generate validation and preview artifacts without mutating raw tables.
- The LLM provider is selected at runtime. A local OpenAI-compatible endpoint and Gemini-compatible providers create the LLM object, but they do not bypass the graph or its validation gates.

## Compiled Graph Order

```text
START
  -> load_schema_metadata
  -> parse_ir_request
  -> build_resolution_recommendations
  -> generate_selection_sql
  -> validate_selection_sql
  -> fetch_target_dataset
  -> lookup_mongodb_rule
  -> choose_rule_or_generate_crud_sql
  -> validate_generated_sql
  -> build_change_preview_json
  -> wait_for_user_confirmation
  -> execute_confirmed_sql
  -> build_execution_result_json
  -> END
```

## Stage Responsibilities

### Stage 01: Parse

`stage_01_parse.py` converts user language into structured IR. It keeps table, condition, action, metric, group-by, derived-value, and linked-step intent separate. It also normalizes common business terms such as metric presence, zero conditions, count-like requests, and media-table hints before SQL generation.

### Stage 02: Rule Lookup

`stage_02_rule_lookup.py` is the reusable-rule placeholder. The local compose setup does not provision MongoDB, so public docs should describe this as a planned or placeholder rule-store boundary unless a real rule store is added.

### Stage 03: SQL

`stage_03_sql.py` compiles deterministic SQL candidates from validated IR and live schema metadata. It handles detail SELECT, aggregate SELECT, numeric UPDATE, and derived-value INSERT preview paths. The validator checks table allowlists, live columns, protected write columns, dangerous tokens, parameter counts, predicates, and linked-step overlay restrictions.

Linked aggregate previews use a compact effective-source relation. Instead of emitting row-by-row `CASE row_id WHEN ...` SQL, the query joins approved dependency delta rows and projects only the columns needed by the current SELECT, aggregate, predicate, and overlay.

### Stage 04: Output

`stage_04_output.py` builds the preview JSON, stores preview delta items for linked steps, waits for explicit confirmation, and executes only when validation, preview, approval, and fingerprint checks all match.

Preview-only paths do not mutate raw staging tables. A final approved UPDATE can mutate raw rows only after matching rows are copied to a backup table and backup coverage is verified.

## Linked-Step Preview Model

Linked requests may contain multiple reviewable parts. Each part has a step id, dependency metadata, expected artifacts, and preview status.

- Independent steps do not inherit each other’s preview deltas.
- Dependent SELECT and aggregate steps can read approved ancestor deltas through the effective-source overlay.
- Dependent UPDATE predicates that would require prior overlay values are blocked until an overlay-aware write path exists.
- Cancelling or changing an upstream step invalidates dependent preview state.

## Confirmation And Execution Gates

Execution requires all of the following:

1. Generated SQL validation passed.
2. A pending preview exists.
3. The reviewer explicitly approved the preview.
4. The approved SQL fingerprint still matches the generated SQL.
5. The approved preview fingerprint still matches the displayed preview.
6. Raw UPDATE candidates have backup coverage before mutation.

If any condition fails, the workflow returns a skipped or blocked execution result instead of running the SQL.

## Test Artifacts

The markdown test runner writes review artifacts under `test/`. Query PASS means the candidate SQL validated and produced a preview, unless deterministic advisory checks find a clear request-SQL-sample mismatch and downgrade the query result to CHECK. Semantic PASS is a separate human review of the natural-language request, generated SQL, and sample evidence. Public docs should report only sanitized aggregate outcomes and avoid including raw samples or private source values.
