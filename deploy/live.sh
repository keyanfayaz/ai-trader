#!/usr/bin/env bash
# Bring the ai-trader stack "live" on a random Cloudflare quick-tunnel domain.
#
#   backend (uvicorn :8000) ─┐
#                            ├─ Caddy :8080  ──  cloudflared  ──  https://<random>.trycloudflare.com
#   frontend (next   :3100) ─┘   (basic auth)      (quick tunnel)
#
# The frontend is built with an empty NEXT_PUBLIC_BACKEND_URL so all API calls
# are relative to the current origin -> the random domain can rotate freely
# without a rebuild. Ctrl-C tears down everything this script started.
#
# Usage:  deploy/live.sh [--no-build]

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/deploy/logs"
SECRETS="$ROOT/deploy/secrets.env"
NO_BUILD=0
[[ "${1:-}" == "--no-build" ]] && NO_BUILD=1

mkdir -p "$LOG_DIR"

if [[ ! -f "$SECRETS" ]]; then
	echo "Missing $SECRETS (basic-auth credentials). Aborting." >&2
	exit 1
fi
set -a
# shellcheck disable=SC1090
source "$SECRETS"
set +a
export AITRADER_USER AITRADER_PASS_HASH

PIDS=()
URL_FILE="$ROOT/deploy/CURRENT_URL.txt"
cleanup() {
	echo
	echo "Shutting down..."
	# Remove the URL marker so a dead quick-tunnel URL is never left behind.
	rm -f "$URL_FILE"
	for pid in "${PIDS[@]}"; do
		kill "$pid" 2>/dev/null || true
	done
	wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

wait_for() { # url, label, tries
	local url="$1" label="$2" tries="${3:-60}"
	for ((i = 0; i < tries; i++)); do
		if curl -fsS -o /dev/null "$url" 2>/dev/null; then
			echo "  $label is up."
			return 0
		fi
		sleep 1
	done
	echo "  Timed out waiting for $label ($url)." >&2
	return 1
}

# --- 1. Backend ----------------------------------------------------------------
if curl -fsS -o /dev/null "http://127.0.0.1:8000/health" 2>/dev/null; then
	echo "Backend already running on :8000 (reusing it)."
else
	echo "Starting backend on :8000 ..."
	(cd "$ROOT/backend" && exec .venv/bin/python -m app.main) \
		>"$LOG_DIR/backend.log" 2>&1 &
	PIDS+=($!)
	wait_for "http://127.0.0.1:8000/health" "backend" 60
fi

# --- 2. Frontend ---------------------------------------------------------------
export NEXT_PUBLIC_BACKEND_URL=""
if [[ $NO_BUILD -eq 0 ]]; then
	echo "Building frontend (NEXT_PUBLIC_BACKEND_URL empty -> relative API calls) ..."
	(cd "$ROOT/frontend" && NEXT_PUBLIC_BACKEND_URL="" npm run build) \
		>"$LOG_DIR/frontend-build.log" 2>&1 \
		|| { echo "Frontend build failed - see $LOG_DIR/frontend-build.log" >&2; exit 1; }
else
	echo "Skipping frontend build (--no-build)."
fi
echo "Starting frontend on :3100 ..."
(cd "$ROOT/frontend" && NEXT_PUBLIC_BACKEND_URL="" exec npm run start -- -p 3100) \
	>"$LOG_DIR/frontend.log" 2>&1 &
PIDS+=($!)
wait_for "http://127.0.0.1:3100" "frontend" 60

# --- 3. Caddy (single origin + basic auth) ------------------------------------
echo "Starting Caddy reverse proxy on :8080 ..."
caddy run --config "$ROOT/deploy/Caddyfile" --adapter caddyfile \
	>"$LOG_DIR/caddy-run.log" 2>&1 &
PIDS+=($!)
sleep 2

# --- 4. localhost.run SSH tunnel ----------------------------------------------
# Account-less, no install (plain ssh), and verified to actually route.
# (Cloudflare account-less quick tunnels were unreliable: dead 530s and edge
#  404s with no uptime guarantee. ngrok is unavailable here - the free plan
#  allows one agent and another project already holds it.)
echo "Opening localhost.run SSH tunnel ..."
: >"$LOG_DIR/tunnel.log"
ssh -o StrictHostKeyChecking=no \
	-o UserKnownHostsFile=/dev/null \
	-o ServerAliveInterval=30 \
	-o ServerAliveCountMax=3 \
	-o ExitOnForwardFailure=yes \
	-R 80:localhost:8080 nokey@localhost.run \
	>"$LOG_DIR/tunnel.log" 2>&1 &
PIDS+=($!)

rm -f "$URL_FILE"
URL=""
for ((i = 0; i < 45; i++)); do
	# Strip ANSI from the localhost.run banner before matching the URL.
	URL="$(sed 's/\x1b\[[0-9;]*m//g' "$LOG_DIR/tunnel.log" 2>/dev/null \
		| grep -Eo 'https://[a-z0-9-]+\.lhr\.life' | head -1 || true)"
	[[ -n "$URL" ]] && break
	sleep 1
done

echo
echo "============================================================"
if [[ -n "$URL" ]]; then
	# Single source of truth for "the URL that is live RIGHT NOW".
	printf '%s\n' "$URL" >"$URL_FILE"
	echo "  LIVE:  $URL"
	echo "         (also saved to deploy/CURRENT_URL.txt - always use that one)"
else
	echo "  Tunnel URL not detected yet - check $LOG_DIR/tunnel.log"
fi
echo "  User:  $AITRADER_USER"
echo "  Pass:  ${AITRADER_PASS:-<see deploy/secrets.env>}"
echo "  Mode:  paper (set in backend/.env)"
echo "============================================================"
echo "  Logs:  $LOG_DIR/   |   Ctrl-C to stop everything."
echo

wait
