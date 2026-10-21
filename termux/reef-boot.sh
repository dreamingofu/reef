#!/data/data/com.termux/files/usr/bin/bash
# Termux:Boot entry point. Also safe to run by hand to (re)start Reef.
termux-wake-lock
cd "$HOME/reef" || exit 1

if [ -f reef.pid ] && kill -0 "$(cat reef.pid)" 2>/dev/null; then
    kill "$(cat reef.pid)"
    sleep 1
fi

nohup python app.py >> reef.log 2>&1 &
echo $! > reef.pid
