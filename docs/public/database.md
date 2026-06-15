# Public Database Notes

This project expects tabular source data to be loaded into relational staging tables before the workflow evaluates natural-language change requests.

## Public Contract

- Staging tables should reflect the current local schema before a workflow run.
- Raw source values should be preserved until validation or transformation rules explicitly handle them.
- Provenance columns may be used locally, but public documentation should describe them generically.
- Identifier-like fields and display-name fields should remain separate unless a validated mapping explicitly proves they are equivalent.

## Validation Principles

Generated SQL should be checked against live database metadata before preview or execution.

Validation should include:

- target table allowlists
- target column allowlists
- parameter count checks
- blocked SQL token checks
- fingerprinting of approved statements
- preview generation before write execution

## What Not To Publish

Do not publish real table samples, row counts, source filenames, source-channel values, production database names, credentials, or business-specific column mappings.

Use placeholders when documentation needs an example:

```sql
SELECT COUNT(*) AS rows FROM <staging_table>;
```
