#!/data/data/com.termux/files/usr/bin/bash
# Termux:Boot entry point (copied to ~/.termux/boot/reef.sh). Also run it by hand to restart Reef:
# it stops the running supervisor (and its app.py), then starts termux/reef-run.sh, which restarts
# app.py with backoff whenever it exits.
command -v termux-wake-lock >/dev/null && termux-wake-lock  # keep Android from sleeping the CPU
cd "${REEF_DIR:-$HOME/reef}" || exit 1

# Command line of a pid: /proc on Android/Termux, ps elsewhere (a Mac, for testing).
cmdline() {
    if [ -r "/proc/$1/cmdline" ]; then tr '\0' ' ' < "/proc/$1/cmdline"; else ps -p "$1" -o args= 2>/dev/null; fi
}

# The supervisor first (so it doesn't restart what we stop), then any app.py it or an older
# nohup-style start left behind. After a reboot a pidfile's number may belong to another process,
# so only a pid whose command line is ours gets killed. Wait up to 10 s for the port to be free.
for entry in "reef-run.pid:reef-run.sh" "reef.pid:app.py"; do
    pidfile=${entry%%:*}
    want=${entry#*:}
    pid=$(cat "$pidfile" 2>/dev/null)
    case "$pid" in ''|*[!0-9]*) rm -f "$pidfile"; continue ;; esac
    case "$(cmdline "$pid")" in
        *"$want"*)
            kill "$pid" 2>/dev/null
            for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$pid" 2>/dev/null || break; sleep 1; done ;;
        *) echo "reef: $pidfile is stale (pid $pid isn't $want), not killing it" ;;
    esac
    rm -f "$pidfile"
done

mkdir -p logs
nohup bash termux/reef-run.sh > /dev/null 2>&1 &
