# Public Setup Guide

This guide describes a generic local setup for the workflow. Replace placeholders with local-only values and keep secrets out of version control.

## Requirements

- Python 3.x
- Docker and Docker Compose
- A local MariaDB-compatible database

## Python Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements-langgraph.txt
python3 -m pip install -r requirements-mariadb.txt
```

## Database

Start the local database service:

```bash
cp .env.example .env
cp compose.example.yaml compose.yaml
docker compose up -d mariadb
```

Use environment variables or an ignored `.env` file for local credentials.

```bash
DB_HOST=127.0.0.1
DB_PORT=3307
DB_USER=<local_user>
DB_PASSWORD=<local_password>
DB_NAME=<local_database>
```

Do not commit real credentials, production database names, exported source data, or local model paths.

## Local Data Loading

Place source files in an ignored local data directory. Use the project loader script that matches your private schema and source layout.

Do not add source files, extracted archives, generated exports, or database backups to version control.

## Manual Workflow UI

```bash
bash scripts/run_streamlit.sh
```

The UI is intended for local review and confirmation testing. Do not capture screenshots containing private data for public documentation.

The default LLM client uses a local OpenAI-compatible endpoint. If you use an external provider, keep API keys in an ignored local `.env` file or enter them only in the local admin session. Do not commit keys, private endpoint names, model files, or screenshots with source data.

When refreshing public screenshots, restart the local UI first and capture only generic screens that do not expose raw source rows, private prompts, credentials, file names, or production identifiers.
