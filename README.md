# Approval-Gated SQL Workflow

This repository contains a reference workflow for loading tabular source files into MariaDB and handling natural-language change requests through a guarded SQL pipeline.

The public version intentionally avoids client names, production identifiers, raw data details, business rules, and confidential schema examples. Keep all private documentation, source data, exports, backups, local model files, and environment files outside version control.

## What It Does

- Loads local CSV/XLSX source files into staging tables.
- Preserves raw values as text until downstream validation decides how to interpret them.
- Uses a staged workflow to parse a requested change, generate SQL, validate it, preview the result, and require explicit approval before execution.
- Blocks unsafe SQL patterns and validates generated statements against the live database schema.
- Provides command-line utilities for local data loading and read-only database inspection.
- Provides a Streamlit interface for manual preview and approval during development.

## Repository Layout

```text
.
├── app/                         # Application and workflow code
├── scripts/                     # Local data-load and DB inspection CLIs
├── docs/public/                 # External-shareable documentation only
├── compose.example.yaml         # Public Docker Compose template
├── .env.example                 # Public environment template
├── requirements-langgraph.txt   # Workflow/UI dependencies
└── requirements-mariadb.txt     # Database client dependencies
```

Internal documentation is ignored by default. Only files under `docs/public/` are intended for external sharing.

## Requirements

- Python 3.x
- Docker and Docker Compose
- MariaDB, provided locally from `compose.example.yaml`

Use environment variables for database credentials and never commit local `.env` files.

```bash
DB_HOST=127.0.0.1
DB_PORT=3307
DB_USER=<user>
DB_PASSWORD=<password>
DB_NAME=<database>
```

The application code may use project-specific environment variable names internally. For public documentation, keep real values and client-specific names out of committed files.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements-langgraph.txt
python3 -m pip install -r requirements-mariadb.txt
```

Prepare local templates, then start the local database service:

```bash
cp .env.example .env
cp compose.example.yaml compose.yaml
docker compose up -d mariadb
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

Run the manual workflow UI:

```bash
streamlit run app/streamlit_langgraph_test.py
```

## Local LLM and Streamlit Launch

The main llama.cpp profile is the currently selected Qwen 14B GGUF model:

- Model directory: `models/unsloth-Qwen3-14B-GGUF/`
- Model file: `Qwen3-14B-UD-Q6_K_XL.gguf`
- Runtime alias: `qwen3-14b`
- Quantization: Unsloth dynamic GGUF `UD-Q6_K_XL`; GGUF tensor metadata includes `q6_K`, `q8_0`, and `f32` tensors.

Minimum `.env` values for the main GPU server:

```bash
SQL_WORKFLOW_LLM_BASE_URL=http://127.0.0.1:8000/v1
SQL_WORKFLOW_LLM_MODEL=qwen3-14b
SQL_WORKFLOW_LLM_MODEL_PATH=models/unsloth-Qwen3-14B-GGUF/Qwen3-14B-UD-Q6_K_XL.gguf
SQL_WORKFLOW_LLAMA_N_GPU_LAYERS=999
SQL_WORKFLOW_LLAMA_CTX_SIZE=8192
SQL_WORKFLOW_LLAMA_HOST=127.0.0.1
SQL_WORKFLOW_LLAMA_PORT=8000
```

Start the 14B llama.cpp GPU server:

```bash
nohup bash scripts/run_llama_cpp_server.sh >/dev/null 2>&1 &
```

The script writes llama.cpp output to `logs/llama_cpp_server.log` and exposes the OpenAI-compatible endpoint at `http://127.0.0.1:8000/v1` by default.

Start the Streamlit approval UI:

```bash
nohup bash scripts/run_streamlit.sh >/dev/null 2>&1 &
```

The Streamlit script defaults to `0.0.0.0:8501` and writes logs to `logs/streamlit.log`. Local access is `http://127.0.0.1:8501`; another PC on the same network can use `http://<server-ip>:8501` if the firewall allows the port.

Stop local services when needed:

```bash
pkill -f 'llama.cpp/build/bin/llama-server'
pkill -f 'streamlit run app/streamlit_langgraph_test.py'
```

## Safety Model

Generated SQL must pass through the workflow before execution:

1. Load current schema metadata.
2. Parse the requested change.
3. Generate a parameterized SQL candidate.
4. Validate target tables, columns, parameters, and unsafe tokens.
5. Build a preview payload.
6. Require explicit approval.
7. Execute only when the approved SQL fingerprint still matches.

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
