#!/usr/bin/env bash
# heartbeat: watch Claude's session; when Claude is out of usage (or silent too long while work is open),
# hand orchestration to the herdr agent "orchestrator" (agy). Afterwards, nudge it every 30 min when idle.
#
#   tools/heartbeat.sh <~/.claude/projects/<proj>/ dir> [interval_s=300] [stale_min=90]
set -u
TDIR=${1:?claude projects dir or transcript}; IV=${2:-300}; STALE=${3:-90}
[ -d "$TDIR" ] || TDIR=$(dirname "$TDIR")  # always follow the newest session file (resumes create new ones)
V=~/01-System/MegaVault/Projects/ChopPad
LOCK=$V/ORCHESTRATOR.lock          # "claude" | "agy <iso time>"
REPO=$(cd "$(dirname "$0")/.." && pwd)
[ -f "$LOCK" ] || echo claude > "$LOCK"
last_nudge=0

takeover() {
  python3 "$REPO/tools/claude_context.py" "$T" --last 80 --out "$V/Planning/claude-context.md"
  echo "agy $(date -Is)" > "$LOCK"
  herdr agent prompt orchestrator "TAKEOVER ($1). Claude is out. Read ~/01-System/MegaVault/Projects/ChopPad/ORCHESTRATOR-HANDOFF.md section TAKEOVER, then Planning/claude-context.md, then continue the queue. Report in Inbox." >/dev/null
  echo "$(date +%H:%M) TAKEOVER sent: $1"
}

while true; do
  T=$(ls -t "$TDIR"/*.jsonl | head -1)
  now=$(date +%s); age=$(( (now - $(stat -c %Y "$T")) / 60 ))
  owner=$(cut -d' ' -f1 "$LOCK")
  # only real API-error entries count (never text Claude wrote about limits)
  limit=$(tail -n 30 "$T" | python3 -c 'import sys,json
n=0
for l in sys.stdin:
    try: e=json.loads(l)
    except Exception: continue
    if e.get("isApiErrorMessage") or (e.get("type")=="system" and "limit" in str(e.get("content","")).lower()): n+=1
print(n)')
  state=$(herdr agent get orchestrator 2>/dev/null | grep -o '"agent_status":"[a-z]*"' | cut -d'"' -f4)
  echo "$(date +%H:%M) ♥ owner=$owner claude_idle=${age}m limit_hits=$limit orchestrator=${state:-missing}"
  if [ "$owner" = claude ]; then
    if [ "$limit" -gt 0 ]; then takeover "usage-limit message in transcript"
    elif [ "$age" -ge "$STALE" ] && [ ! -f "$V/Planning/.claude-done" ]; then takeover "claude silent ${age}m with open work"
    fi
  elif [ "$owner" = agy ] && [ "$state" = idle -o "$state" = done ] && [ $((now - last_nudge)) -ge 1800 ]; then
    herdr agent prompt orchestrator "HEARTBEAT: check Inbox (unread), tools/status.py --vault, worker panes (herdr agent list); unblock/assign next tasks per ORCHESTRATOR-HANDOFF.md. If everything is done or waiting on neo, say so in Inbox and stay idle." >/dev/null
    last_nudge=$now; echo "$(date +%H:%M) nudged orchestrator"
  fi
  sleep "$IV"
done
