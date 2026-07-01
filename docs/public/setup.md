# Public Setup Guide

This guide describes a generic local setup for the workflow. Replace placeholders with local-only values and keep secrets out of version control.

## Requirements

- Docker and Docker Compose
- Optional: Python 3.x for host-side utility scripts

## Docker-First Setup

```bash
cp .env.example .env
# Edit .env locally. Do not commit it.
docker compose up --build
```

After pulling updates:

```bash
git pull
docker compose up --build
```

The Compose stack starts MariaDB and the Streamlit review UI. Configure LLM
provider settings in the ignored `.env` file. A local model file is not shipped
with the repository.

Use environment variables or an ignored `.env` file for local credentials.

```bash
SQL_WORKFLOW_DB_HOST=127.0.0.1
SQL_WORKFLOW_DB_PORT=3307
SQL_WORKFLOW_DB_USER=<local_user>
SQL_WORKFLOW_DB_PASSWORD=<local_password>
SQL_WORKFLOW_DB_NAME=<local_database>
```

Do not commit real credentials, production database names, exported source data, or local model paths.

## Optional Host Python Environment

Use this only when running scripts outside Docker:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

## Local Data Loading

Place source files in an ignored local data directory. Use the project loader script that matches your private schema and source layout.

Do not add source files, extracted archives, generated exports, or database backups to version control.

## Manual Workflow UI

```bash
bash scripts/run_streamlit.sh
```

The UI is intended for local review and confirmation testing. Do not capture screenshots containing private data for public documentation.

The LLM client is configured through environment variables. If you use an external provider, keep API keys in an ignored local `.env` file or enter them only in the local admin session. Do not commit keys, private endpoint names, model files, or screenshots with source data.

When refreshing public screenshots, restart the local UI first and capture only generic screens that do not expose raw source rows, private prompts, credentials, file names, or production identifiers.
