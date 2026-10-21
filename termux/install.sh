#!/data/data/com.termux/files/usr/bin/bash
# One-shot Reef installer for Termux. Re-running it updates Reef (git pull) and restarts it.
set -e

REPO="https://github.com/dreamingofu/reef.git"
DIR="$HOME/reef"
MUSIC="$HOME/storage/shared/Music"

echo "=== Reef install ==="

echo "[1/6] Packages"
pkg install -y python ffmpeg git

echo "[2/6] Code"
if [ -d "$DIR/.git" ]; then
    git -C "$DIR" pull --ff-only
else
    git clone "$REPO" "$DIR"
fi

echo "[3/6] Python deps"
pip install -q -r "$DIR/requirements.txt"

echo "[4/6] Storage check"
if [ ! -d "$HOME/storage/shared" ]; then
    echo "Shared storage not set up. Run: termux-setup-storage  (tap Allow), then re-run this script."
    exit 1
fi
mkdir -p "$MUSIC/Reef"
if ! touch "$MUSIC/Reef/.write-test" 2>/dev/null; then
    echo "Cannot write to $MUSIC."
    echo "Fix: Android Settings > Apps > Termux > Permissions > Files and media > Allow, then re-run."
    exit 1
fi
rm -f "$MUSIC/Reef/.write-test"
if [ "$(settings get global settings_enable_monitor_phantom_procs 2>/dev/null)" = "true" ]; then
    echo "Warning: Android phantom process killer is ON; background apps may get killed."
    echo "Fix from a PC: adb shell settings put global settings_enable_monitor_phantom_procs false"
fi

echo "[5/6] Config"
if [ ! -f "$DIR/reef.env" ]; then
    read -rp "Navidrome admin username: " ND_USER
    read -rsp "Navidrome admin password: " ND_PASS
    echo
    cat > "$DIR/reef.env" <<EOF
REEF_PORT=5274
REEF_MUSIC_DIR=$MUSIC
REEF_NAVIDROME_URL=http://127.0.0.1:4533
REEF_NAVIDROME_USER=$ND_USER
REEF_NAVIDROME_PASS=$ND_PASS
REEF_PASSWORD=
EOF
    chmod 600 "$DIR/reef.env"
else
    echo "Keeping existing $DIR/reef.env"
fi

echo "[6/6] Autostart + launch"
mkdir -p "$HOME/.termux/boot"
cp "$DIR/termux/reef-boot.sh" "$HOME/.termux/boot/reef.sh"
chmod +x "$HOME/.termux/boot/reef.sh"
"$HOME/.termux/boot/reef.sh"

sleep 4
echo
if curl -sf http://127.0.0.1:5274/api/health; then
    echo
    echo "========================================="
    echo "  Reef is running on port 5274"
    echo "========================================="
    echo "On your iPhone, open: http://<this phone's Tailscale IP>:5274"
    echo "Tailscale IP: $(ip -4 addr show tailscale0 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1 || echo 'see Tailscale app')"
else
    echo "Reef did not start. Last log lines:"
    tail -20 "$DIR/reef.log"
    exit 1
fi
