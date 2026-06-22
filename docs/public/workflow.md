# Public Workflow Guide

The workflow uses a guarded graph to turn a natural-language change request into a reviewed SQL operation.

## Checkpoint 1: Load Metadata

The workflow reads current database metadata before generating SQL. This prevents stale assumptions about available tables, columns, and source values.

## Checkpoint 2: Parse Request

The natural-language request is converted into an internal representation of intent, filters, target fields, and requested changes.

The parsed result is not trusted by itself. It is only an intermediate proposal for later validation.

For multi-sentence requests, the parser may split the request into linked parts. Independent parts can be reviewed separately. Dependent parts must wait for their prerequisite part to be confirmed before their impact view is considered current.

## Checkpoint 2.5: Recommendation Candidates

After parsing, the workflow can create recommendation-only candidates for ambiguous terms. Recommendations do not mutate the original request, IR, SQL candidate, or confirmation state. Selecting a recommendation starts a new review run with execution disabled.

## Checkpoint 3: Generate And Validate SQL

The workflow builds a parameterized SQL candidate and validates it before showing sample impact.

Validation should reject unsafe statements, unknown tables, unknown columns, parameter mismatches, and attempts to bypass the approved mutation path.

## Checkpoint 4: Build Sample Impact View

Before execution, the system produces a structured impact view that a reviewer can inspect. Public documentation should describe this view generically without exposing private examples.

## Checkpoint 5: Confirm Or Skip

Execution requires explicit confirmation. Confirmation should be tied to the exact SQL candidate through a fingerprint or equivalent integrity check.

Linked-part confirmation must remain review-first. Bulk confirmation should only confirm impact views that have already been generated and shown to the reviewer.

## Checkpoint 6: Execute Guarded Change

The workflow executes only if validation passed, an impact view exists, confirmation was granted, and the confirmed fingerprint still matches the candidate statement.

## Safety Checklist

- Do not execute generated SQL directly.
- Do not skip sample-impact generation.
- Do not trust model output without schema validation.
- Do not publish real prompts, filters, table values, or result examples.
- Keep public examples generic and placeholder-based.
- Do not auto-apply fuzzy recommendations.
- Do not execute a linked part that has not been reviewed.
