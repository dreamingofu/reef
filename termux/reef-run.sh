#!/data/data/com.termux/files/usr/bin/bash
# Keeps Reef running: starts `python app.py`, and when it exits (crash, OOM kill, yt-dlp hang killed by hand)
# starts it again after 2, 4, 8 ... 60 s. A run that stayed up 5 min resets the wait, so one crash a day
# restarts in 2 s while a crash loop doesn't spin. Started in the background by reef-boot.sh.
#
# Files (in the Reef folder): reef-run.pid = this loop, reef.pid = the current app.py, logs/reef-run.log =
# restarts, logs/reef.out = the app's console output (start-up errors; the app logs to logs/reef.log itself).
# Stop it with: kill "$(cat reef-run.pid)"   (it stops app.py too).
#
# Test knobs: REEF_RUN_CMD (command instead of `python app.py`), REEF_RUN_MIN / REEF_RUN_MAX (backoff, s),
# REEF_RUN_STABLE (s of uptime that resets the backoff).

cd "$(dirname "$0")/.." || exit 1
MIN=${REEF_RUN_MIN:-2}
MAX=${REEF_RUN_MAX:-60}
STABLE=${REEF_RUN_STABLE:-300}
CMD=${REEF_RUN_CMD:-python app.py}
mkdir -p logs
# The app writes logs/reef.log (rotated) itself; a console copy would make reef.out grow without bound.
export REEF_LOG_STDERR=0

note() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> logs/reef-run.log
    # Keep the restart log small: the last 200 lines once it passes ~100 KB.
    if [ "$(wc -c < logs/reef-run.log)" -gt 100000 ]; then
        tail -n 200 logs/reef-run.log > logs/reef-run.log.tmp && mv logs/reef-run.log.tmp logs/reef-run.log
    fi
}

child=""
sleeper=""
stop() {
    note "supervisor stop pid=$$ child=${child:-none}"
    [ -n "$sleeper" ] && kill "$sleeper" 2>/dev/null
    if [ -n "$child" ]; then
        kill "$child" 2>/dev/null
        wait "$child" 2>/dev/null
    fi
    rm -f reef.pid reef-run.pid
    exit 0
}
trap stop TERM INT HUP

echo $$ > reef-run.pid
note "supervisor start pid=$$ cmd=$CMD"
delay=$MIN
while :; do
    [ -f logs/reef.out ] && mv -f logs/reef.out logs/reef.out.1  # keep the previous run's output, no more
    start=$(date +%s)
    $CMD > logs/reef.out 2>&1 &
    child=$!
    echo "$child" > reef.pid
    note "reef start pid=$child"
    wait "$child"
    code=$?
    child=""
    up=$(( $(date +%s) - start ))
    [ "$up" -ge "$STABLE" ] && delay=$MIN
    note "reef exit code=$code up_s=$up restart_in_s=$delay"
    # Backgrounded so a TERM during the wait runs stop() at once instead of after the sleep.
    sleep "$delay" &
    sleeper=$!
    wait "$sleeper"
    sleeper=""
    delay=$(( delay * 2 ))
    [ "$delay" -gt "$MAX" ] && delay=$MAX
done
