#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

MODEL_PATH="${SQL_WORKFLOW_LLM_MODEL_PATH:-models/local-model/model.gguf}"
MODEL_ALIAS="${SQL_WORKFLOW_LLM_MODEL:-local-model}"
N_GPU_LAYERS="${SQL_WORKFLOW_LLAMA_N_GPU_LAYERS:-999}"
CTX_SIZE="${SQL_WORKFLOW_LLAMA_CTX_SIZE:-8192}"
HOST="${SQL_WORKFLOW_LLAMA_HOST:-127.0.0.1}"
PORT="${SQL_WORKFLOW_LLAMA_PORT:-8000}"
LOG_DIR="${SQL_WORKFLOW_LLAMA_LOG_DIR:-logs}"
LOG_FILE="$LOG_DIR/llama_cpp_server.log"
SERVER_BIN="${SQL_WORKFLOW_LLAMA_SERVER_BIN:-llama.cpp/build/bin/llama-server}"

mkdir -p "$LOG_DIR"

exec "$SERVER_BIN" \
  -m "$MODEL_PATH" \
  --alias "$MODEL_ALIAS" \
  --host "$HOST" \
  --port "$PORT" \
  -ngl "$N_GPU_LAYERS" \
  -c "$CTX_SIZE" \
  --chat-template-kwargs '{"enable_thinking":false}' \
  --reasoning-budget 0 \
  --temp 0.2 \
  --top-p 0.9 \
  --top-k 40 \
  --min-p 0.0 \
  --presence-penalty 0.0 \
  --repeat-penalty 1.05 \
  >> "$LOG_FILE" 2>&1
