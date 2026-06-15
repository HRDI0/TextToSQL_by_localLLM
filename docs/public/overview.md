# Public Overview

This project demonstrates a guarded workflow for applying natural-language change requests to tabular data stored in a relational database.

The system is designed for datasets that require careful review before mutation. Instead of sending generated SQL directly to the database, the workflow builds a staged decision trail: parse the request, generate a candidate statement, validate it against live metadata, preview the impact, and execute only after explicit approval.

## Goals

- Keep raw input data separate from public source code.
- Treat generated SQL as untrusted until validated.
- Require a human-readable preview before any write operation.
- Preserve an approval boundary between proposal and execution.
- Make local development reproducible without publishing private datasets or business rules.

## High-Level Architecture

```text
Source files -> Loader CLI -> MariaDB -> Workflow graph -> Preview -> Approval -> Execution
```

The workflow code is organized as small stages so validation, preview generation, and execution gates can be reviewed independently.

## Public Sharing Boundary

Only generic architecture, setup instructions, and safety behavior belong in public documentation. Do not publish client names, production identifiers, raw file names, source examples, prompt examples copied from real work, or local runtime paths.
