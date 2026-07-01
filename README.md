# Review-Gated SQL Workflow

This repository contains a reference workflow for loading tabular source files into MariaDB and handling natural-language change requests through a guarded SQL pipeline.

The public version intentionally avoids client names, production identifiers, raw data details, business rules, and confidential schema examples. Keep all private documentation, source data, exports, backups, local model files, and environment files outside version control.

## What It Does

- Loads local CSV/XLSX source files into staging tables.
- Preserves raw values as text until downstream validation decides how to interpret them.
- Uses a guarded review flow to parse a requested change, generate SQL, validate it, show sample impact, and require an explicit confirmation before execution.
- Blocks unsafe SQL patterns and validates generated statements against the live database schema.
- Provides command-line utilities for local data loading and read-only database inspection.
- Provides a Streamlit interface where users can inspect sample rows and confirm or skip changes during development.

## Repository Layout

```text
.
├── app/                         # Application and workflow code
├── scripts/                     # Local data-load and DB inspection CLIs
├── docs/public/                 # External-shareable documentation only
├── compose.yaml                 # Docker Compose stack for MariaDB and Streamlit
├── .env.example                 # Public environment template
└── requirements.txt             # Python dependencies
```

Internal documentation is ignored by default. Only files under `docs/public/` are intended for external sharing.

## Requirements

- Docker and Docker Compose
- Optional: Python 3.x for running scripts outside Docker

Use environment variables for database credentials and never commit local `.env` files.

```bash
SQL_WORKFLOW_DB_HOST=127.0.0.1
SQL_WORKFLOW_DB_PORT=3307
SQL_WORKFLOW_DB_USER=<user>
SQL_WORKFLOW_DB_PASSWORD=<password>
SQL_WORKFLOW_DB_NAME=<database>
```

Keep real values and client-specific names out of committed files.

## Docker Setup

```bash
cp .env.example .env
# Edit .env locally. Do not commit it.
docker compose up --build
```

After a later update:

```bash
git pull
docker compose up --build
```

The Docker stack starts MariaDB and the Streamlit app. For the current local
workflow, use Gemini native mode in `.env` and fill one of the API key fields:

```bash
SQL_WORKFLOW_LLM_PROVIDER=gemini_native
SQL_WORKFLOW_GEMINI_MODEL=gemini-3.1-flash-lite
GOOGLE_API_KEY=
GEMINI_API_KEY=
```

Leave unused key fields blank. A local model file is not shipped with the repository.
Docker Compose passes these values from `.env` into the app container; you do
not need to export API keys in your shell.

For host-side Python utilities, optionally create a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

## Local Usage

Prepare source files in a local, ignored data directory, then run the loader script that matches your private schema and source layout. Do not document real source paths, file names, channel names, or customer-specific loader options in public docs.

The loader command is intentionally omitted from the public README because it can reveal private dataset structure. Keep operational runbooks in ignored internal documentation.

Inspect the local database with the read-only helper:

```bash
python3 scripts/query_db.py summary
python3 scripts/query_db.py sample --limit 10
python3 scripts/query_db.py sql "SELECT COUNT(*) AS rows FROM <table_name>"
```

The Streamlit UI is exposed at `http://127.0.0.1:8501` by default. From the host,
MariaDB is exposed at `127.0.0.1:3307`; inside Docker, the app uses `mariadb:3306`.

Run the manual workflow UI directly on the host only if you installed local Python dependencies:

```bash
bash scripts/run_streamlit.sh
```

The UI first fixes the requested data scope, then applies follow-up conditions or calculations inside that scope. The LLM connection is configured through environment variables; optional provider/model selection is available in the local admin controls without committing secrets.

## Local LLM and Streamlit Launch

The optional local LLM path is an OpenAI-compatible llama.cpp server. Keep actual model paths, private endpoints, and hardware-specific settings in your ignored local `.env` or internal runbook.

Minimum `.env` values for the main GPU server:

```bash
SQL_WORKFLOW_LLM_BASE_URL=http://127.0.0.1:8000/v1
SQL_WORKFLOW_LLM_MODEL=<local_model_alias>
SQL_WORKFLOW_LLM_MODEL_PATH=<ignored_local_model_path>
SQL_WORKFLOW_LLAMA_N_GPU_LAYERS=<local_gpu_layer_count>
SQL_WORKFLOW_LLAMA_CTX_SIZE=8192
SQL_WORKFLOW_LLAMA_HOST=127.0.0.1
SQL_WORKFLOW_LLAMA_PORT=8000
```

Start the optional llama.cpp server:

```bash
nohup bash scripts/run_llama_cpp_server.sh >/dev/null 2>&1 &
```

The script writes llama.cpp output to `logs/llama_cpp_server.log` and exposes the configured OpenAI-compatible endpoint locally by default.

Start the Streamlit review UI:

```bash
nohup bash scripts/run_streamlit.sh >/dev/null 2>&1 &
```

The Streamlit script defaults to `127.0.0.1:8501` and writes logs to `logs/streamlit.log`. Expose it on a network only behind your own authentication/VPN controls.

Each workflow preview also appends a JSONL audit entry to `logs/workflow_preview_audit.jsonl`. The entry includes the original request text, parsed request interpretation, rendered query metadata, sample result payload, and linked-step result metadata so failed previews can be reviewed later without changing raw tables.

For linked requests, approved preview changes are stored in `rule_engine_delta_item` and later read through a compact join-based overlay. Follow-up SELECT or aggregate previews query raw rows plus approved dependency-ancestor delta rows by `linked_plan_id`, dependency step key, step order, row id, and changed column; they do not generate row-by-row `CASE row_id WHEN ...` SQL for large result sets. Dependent UPDATE predicates that would need prior delta overlay are blocked until an overlay-aware write path is available.

Stop local services when needed:

```bash
pkill -f '<local_llama_server_process_pattern>'
pkill -f 'scripts/run_streamlit.sh|streamlit.*app/streamlit_langgraph_test.py'
```

## Safety Model

Generated SQL must pass through the workflow before execution:

1. Load current schema metadata.
2. Parse the requested change.
3. Generate a parameterized SQL candidate.
4. Validate target tables, columns, parameters, and unsafe tokens.
5. Build a sample impact payload for human review.
6. Require explicit confirmation.
7. Execute only when the confirmed SQL fingerprint still matches.

Preview-only tests and step-by-step review do not mutate raw `DA` or `SA` tables. A final approved UPDATE can mutate raw tables only after every matching raw row has first been copied to `rule_engine_raw_update_backup` with the preview fingerprint as the backup scope; if the backup table is missing or coverage is incomplete, execution fails before the UPDATE.

For operational deployments, keep stable raw-row identity, protected-column policy, canonical typed analysis tables, and execution logs in the private database schema. Do not mutate raw provenance columns directly.

Do not bypass this sequence for production-like data.

## Public Documentation Policy

Before committing documentation, confirm that it contains none of the following:

- Client names or abbreviations
- Production database names, account names, file names, or provenance values
- Raw data samples or screenshots
- Business rules that identify a client workflow
- Credentials, tokens, endpoint names, backup paths, or model paths
- Internal prompts, agent notes, or local knowledge-base files

Place public-safe documentation in `docs/public/`. Keep internal notes outside version control or under ignored paths.
