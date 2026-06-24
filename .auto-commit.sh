#!/bin/bash
REPO_DIR="/home/ubuntu/ai-vuln-system"
LOCK_FILE="/tmp/auto-commit.lock"
DEBOUNCE_SEC=10
LOG_FILE="/tmp/auto-commit.log"

log() { echo "$(date '+%H:%M:%S') $1" >> "$LOG_FILE"; }

cd "$REPO_DIR" || exit 1

log "Watching $REPO_DIR for changes..."

inotifywait -m -r -q   --exclude '(\.git/|__pycache__|\.pyc$|data/sessions|data/db\.sqlite3|\.log$)'   -e modify -e create -e delete -e move   --timefmt '%s' --format '%T'   "$REPO_DIR" | while read -r timestamp; do

  # Skip if debounce file is too recent
  if [ -f "$LOCK_FILE" ]; then
    last=$(cat "$LOCK_FILE" 2>/dev/null)
    now=$(date +%s)
    if [ -n "$last" ] && [ $((now - last)) -lt $DEBOUNCE_SEC ]; then
      continue
    fi
  fi

  date +%s > "$LOCK_FILE"
  sleep $DEBOUNCE_SEC

  # Check if lock file changed during sleep (another event reset the timer)
  if [ "$(cat "$LOCK_FILE" 2>/dev/null)" != "$timestamp" ]; then
    continue
  fi

  cd "$REPO_DIR" || continue

  if [ -z "$(git status --porcelain)" ]; then
    continue
  fi

  git add -A 2>>"$LOG_FILE"
  MSG="auto: $(date '+%Y-%m-%d %H:%M:%S') — $(git diff --cached --stat | tail -1 | xargs)"
  git commit -m "$MSG" 2>>"$LOG_FILE"

  if [ $? -eq 0 ]; then
    log "Committed: $MSG"
    git push origin master 2>>"$LOG_FILE" && log "Pushed to origin" || log "Push FAILED"
  fi
done
