# Public Workflow Guide

The workflow uses a staged graph to turn a natural-language change request into a reviewed SQL operation.

## Stage 1: Load Metadata

The workflow reads current database metadata before generating SQL. This prevents stale assumptions about available tables, columns, and source values.

## Stage 2: Parse Request

The natural-language request is converted into an internal representation of intent, filters, target fields, and requested changes.

The parsed result is not trusted by itself. It is only an intermediate proposal for later validation.

## Stage 3: Generate And Validate SQL

The workflow builds a parameterized SQL candidate and validates it before preview.

Validation should reject unsafe statements, unknown tables, unknown columns, parameter mismatches, and attempts to bypass the approved mutation path.

## Stage 4: Build Preview

Before execution, the system produces a structured preview that a reviewer can inspect. The preview should explain what would change without exposing private examples in public documentation.

## Stage 5: Approve Or Reject

Execution requires explicit approval. Approval should be tied to the exact SQL candidate through a fingerprint or equivalent integrity check.

## Stage 6: Execute Guarded Change

The workflow executes only if validation passed, a preview exists, approval was granted, and the approved fingerprint still matches the candidate statement.

## Safety Checklist

- Do not execute generated SQL directly.
- Do not skip preview generation.
- Do not trust model output without schema validation.
- Do not publish real prompts, filters, table values, or result previews.
- Keep public examples generic and placeholder-based.
