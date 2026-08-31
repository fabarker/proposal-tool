#!/usr/bin/env bash
# Stop the services started by start_dashboard.sh.

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$BASE_DIR/var/run"

for name in dashboard_frontend pmg_service; do
    pidfile="$RUN_DIR/$name.pid"
    if [ -f "$pidfile" ]; then
        pid="$(cat "$pidfile")"
        if kill -0 "$pid" 2>/dev/null; then
            echo "Stopping $name (pid $pid)"
            kill "$pid" 2>/dev/null || true
            # uvicorn --workers forks; take the process group down too
            pkill -P "$pid" 2>/dev/null || true
        fi
        rm -f "$pidfile"
    fi
done
echo "Stopped."
