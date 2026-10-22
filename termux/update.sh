#!/data/data/com.termux/files/usr/bin/bash
# Update Reef on the phone and restart it, rolling back when the new version doesn't come up:
#   bash ~/reef/termux/update.sh [--log FILE] [--dry-run]
# Steps: git pull --ff-only, pip install -r requirements.txt, copy termux/reef-boot.sh to ~/.termux/boot/reef.sh,
# restart through it, wait for /api/health (and the new commit in /api/diagnostics). Prints one last line:
#   UPDATE_OK <sha>                 the new version answers
#   UPDATE_FAILED <reason>          nothing changed (pull failed), or the previous commit was restored and restarted
# Exit status 0 / 1 to match. --log FILE appends everything (also) to FILE; --dry-run only prints the steps.
#
# Test knobs: REEF_DIR (the checkout; default: this script's repo), REEF_UPDATE_BOOT_DIR (~/.termux/boot),
# REEF_UPDATE_PIP (pip command), REEF_UPDATE_WAIT (seconds, 30), REEF_UPDATE_HEALTH_CMD (a command that succeeds
# once Reef is up, instead of the curl checks).

# Everything runs inside main(), which bash reads whole before running it: `git pull` may rewrite this very file,
# and bash reads a plain script while it executes it.
main() {
    local log="" dry=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --log) log=$2; shift 2 ;;
            --log=*) log=${1#--log=}; shift ;;
            --dry-run) dry=1; shift ;;
            -h|--help) sed -n '2,9p' "$0"; return 0 ;;
            *) echo "UPDATE_FAILED unknown option $1"; return 1 ;;
        esac
    done
    if [ -n "$log" ]; then
        mkdir -p "$(dirname "$log")"
        exec > >(tee -a "$log") 2>&1
        TEE_PID=$!
    fi

    local dir boot_dir pip wait_s port
    dir=${REEF_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
    cd "$dir" || { echo "UPDATE_FAILED no Reef checkout at $dir"; return 1; }
    export REEF_DIR="$dir"  # reef-boot.sh starts the checkout it's told about (default ~/reef)
    boot_dir=${REEF_UPDATE_BOOT_DIR:-$HOME/.termux/boot}
    pip=${REEF_UPDATE_PIP:-pip}
    wait_s=${REEF_UPDATE_WAIT:-30}
    port=${REEF_PORT:-$(sed -n 's/^REEF_PORT=["'\'']*\([0-9]*\).*/\1/p' reef.env 2>/dev/null | tail -n 1)}
    port=${port:-5274}

    echo "$(date '+%Y-%m-%d %H:%M:%S') reef update in $dir"
    local prev new
    prev=$(git rev-parse HEAD) || { echo "UPDATE_FAILED not a git checkout"; return 1; }

    if [ "$dry" = 1 ]; then
        echo "+ git pull --ff-only   (now at $(git rev-parse --short HEAD))"
        echo "+ $pip install -q -r requirements.txt"
        echo "+ cp termux/reef-boot.sh $boot_dir/reef.sh && chmod +x $boot_dir/reef.sh"
        echo "+ $boot_dir/reef.sh   (restart), then wait <= ${wait_s}s for http://127.0.0.1:$port/api/health"
        echo "+ on failure: git reset --keep ${prev:0:7}, pip install, restart"
        echo "DRY_RUN"
        return 0
    fi

    if ! git pull --ff-only; then
        echo "UPDATE_FAILED git pull --ff-only failed (nothing changed; Reef keeps running)"
        return 1
    fi
    new=$(git rev-parse HEAD)
    [ "$new" = "$prev" ] && echo "already up to date at ${new:0:7}; restarting anyway" \
        || echo "updated ${prev:0:7} -> ${new:0:7}"

    local reason=""
    if ! $pip install -q -r requirements.txt; then
        reason="pip install failed"
    elif ! install_boot "$boot_dir"; then
        reason="couldn't copy the boot script to $boot_dir"
    else
        restart "$boot_dir"
        if wait_healthy "$port" "$wait_s" "$(git rev-parse --short HEAD)"; then
            echo "UPDATE_OK $(git rev-parse --short HEAD)"
            return 0
        fi
        reason="Reef didn't answer /api/health within ${wait_s}s (see logs/reef.out, logs/reef-run.log)"
    fi

    # Roll back: the previous commit, its dependencies and boot script, restarted.
    echo "rolling back to ${prev:0:7}: $reason"
    if ! git reset --keep "$prev"; then
        echo "UPDATE_FAILED $reason; rollback failed (local changes?), still at $(git rev-parse --short HEAD)"
        return 1
    fi
    $pip install -q -r requirements.txt || echo "warning: pip install failed for ${prev:0:7}"
    install_boot "$boot_dir" || echo "warning: couldn't restore the boot script"
    restart "$boot_dir"
    if wait_healthy "$port" "$wait_s" "$(git rev-parse --short HEAD)"; then
        echo "UPDATE_FAILED $reason; rolled back to ${prev:0:7}, which is running"
    else
        echo "UPDATE_FAILED $reason; rolled back to ${prev:0:7}, but it isn't answering either"
    fi
    return 1
}

install_boot() {
    mkdir -p "$1" && cp termux/reef-boot.sh "$1/reef.sh" && chmod +x "$1/reef.sh"
}

restart() {
    # reef-boot.sh stops the running supervisor + app (waiting for them) and starts the supervisor in the background.
    bash "$1/reef.sh" || echo "warning: the boot script exited with $?"
}

# Up = /api/health answers and /api/diagnostics reports the expected commit (not the old process still exiting).
healthy() {
    if [ -n "$REEF_UPDATE_HEALTH_CMD" ]; then
        bash -c "$REEF_UPDATE_HEALTH_CMD"
        return
    fi
    curl -fsS -m 3 "http://127.0.0.1:$1/api/health" > /dev/null 2>&1 || return 1
    curl -fsS -m 5 "http://127.0.0.1:$1/api/diagnostics" 2>/dev/null | grep -q "\"reef\": *\"$2\""
}

wait_healthy() {
    local port=$1 limit=$2 sha=$3 start
    start=$(date +%s)
    while [ $(( $(date +%s) - start )) -lt "$limit" ]; do
        healthy "$port" "$sha" && return 0
        sleep 1
    done
    healthy "$port" "$sha"
}

# With --log, close our end of the pipe and let tee write the last lines before exiting.
TEE_PID=""
main "$@"; code=$?; if [ -n "$TEE_PID" ]; then exec >&- 2>&-; wait "$TEE_PID" 2>/dev/null; fi; exit $code
