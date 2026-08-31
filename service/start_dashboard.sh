#!/usr/bin/env bash
# Start the epsilon-phi mirror of the cyrus_pmg dashboard:
#   [1/2] isgPMGService (FastAPI, uvicorn)
#   [2/2] dashboardFrontend (Flask static server + /api proxy)
# Mirrors the host's start_dashboard.sh, minus the optimizationService the
# Proposal Tool does not use.

set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"        # .../proposal-tool/service
REPO_ROOT="$(cd "$BASE_DIR/../.." && pwd)"                      # .../epsilon-phi-core

# shellcheck disable=SC1091
[ -f "$BASE_DIR/dashboard.env.defaults" ] && source "$BASE_DIR/dashboard.env.defaults"

export PYTHONPATH="$BASE_DIR:$REPO_ROOT/src/python${PYTHONPATH:+:$PYTHONPATH}"
PY="${PYTHON:-python3}"

LOG_DIR="$BASE_DIR/var/log"
RUN_DIR="$BASE_DIR/var/run"
mkdir -p "$LOG_DIR" "$RUN_DIR"

if [ -f "$RUN_DIR/pmg_service.pid" ] && kill -0 "$(cat "$RUN_DIR/pmg_service.pid")" 2>/dev/null; then
    echo "isgPMGService already running (pid $(cat "$RUN_DIR/pmg_service.pid")). Run stop_dashboard.sh first."
    exit 1
fi

echo "[1/2] Starting isgPMGService (port ${PMG_SVC_PORT}, adapter ${SCENARIO_ADAPTER})..."
nohup "$PY" -m uvicorn cyrus_pmg.pmgService.isgPMGService:app \
    --host "${PMG_SVC_HOST:-127.0.0.1}" --port "${PMG_SVC_PORT}" \
    --workers "${PMG_SVC_WORKERS}" \
    > "$LOG_DIR/pmg_service.log" 2>&1 &
echo $! > "$RUN_DIR/pmg_service.pid"

printf "      waiting for /health"
for _ in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:${PMG_SVC_PORT}/health" >/dev/null 2>&1; then
        printf " up\n"; break
    fi
    printf "."; sleep 1
done

echo "[2/2] Starting Flask frontend (port ${FRONTEND_PORT})..."
nohup "$PY" "$BASE_DIR/cyrus_pmg/dashboard/dashboardFrontend.py" \
    > "$LOG_DIR/dashboard_frontend.log" 2>&1 &
echo $! > "$RUN_DIR/dashboard_frontend.pid"

printf "      waiting for /health"
for _ in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:${FRONTEND_PORT}/health" >/dev/null 2>&1; then
        printf " up\n"; break
    fi
    printf "."; sleep 1
done

echo
echo "Dashboard:     http://localhost:${FRONTEND_PORT}/"
echo "Proposal Tool: http://localhost:${FRONTEND_PORT}/proposalTool/proposalTool.html"
echo "Logs:          $LOG_DIR/"
