#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
if [ -f "${ARIA_ENV_FILE:-.env}" ]; then
    set -a
    source "${ARIA_ENV_FILE:-.env}"
    set +a
fi
python_bin="${ARIA_PYTHON:-$project_dir/.venv/bin/python}"
session="${ARIA_TMUX_SESSION:-aria-guidance}"
mkdir -p .runtime/logs
case "${1:-start}" in
    run)
        exec "$python_bin" -m aria_guidance.cli
        ;;
    start)
        if tmux has-session -t "$session" 2>/dev/null; then
            echo "$session is already running"
            exit 0
        fi
        "$python_bin" -c 'import os,socket; s=socket.socket(); s.bind((os.getenv("ARIA_HOST","127.0.0.1"), int(os.getenv("ARIA_PORT","8785")))); s.close()'
        printf -v command 'cd %q && ARIA_ENV_FILE=%q ARIA_PYTHON=%q bash scripts/service.sh run >> .runtime/logs/service.log 2>&1' \
            "$project_dir" "${ARIA_ENV_FILE:-.env}" "$python_bin"
        tmux new-session -d -s "$session" "$command"
        echo "Started $session; see .runtime/logs/service.log and /api/ready"
        ;;
    status)
        tmux has-session -t "$session"
        curl --noproxy '*' -fsS "http://127.0.0.1:${ARIA_PORT:-8785}/api/health"
        ;;
    stop)
        tmux kill-session -t "$session"
        ;;
    *) echo 'Usage: scripts/service.sh start|run|status|stop' >&2; exit 2 ;;
esac
