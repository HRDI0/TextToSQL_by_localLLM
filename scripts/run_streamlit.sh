#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

HOST="${SQL_WORKFLOW_STREAMLIT_HOST:-127.0.0.1}"
PORT="${SQL_WORKFLOW_STREAMLIT_PORT:-8501}"
LOG_DIR="${SQL_WORKFLOW_STREAMLIT_LOG_DIR:-${SQL_WORKFLOW_LLAMA_LOG_DIR:-logs}}"
LOG_FILE="$LOG_DIR/streamlit.log"
STREAMLIT_BIN="${SQL_WORKFLOW_STREAMLIT_BIN:-.venv/bin/streamlit}"

export SQL_WORKFLOW_LLM_BASE_URL="${SQL_WORKFLOW_LLM_BASE_URL:-http://127.0.0.1:8000/v1}"
export SQL_WORKFLOW_LLM_MODEL="${SQL_WORKFLOW_LLM_MODEL:-qwen3-14b}"
export SQL_WORKFLOW_LLM_API_KEY="${SQL_WORKFLOW_LLM_API_KEY:-EMPTY}"

mkdir -p "$LOG_DIR"

exec "$STREAMLIT_BIN" run app/streamlit_langgraph_test.py \
  --server.address "$HOST" \
  --server.port "$PORT" \
  >> "$LOG_FILE" 2>&1
