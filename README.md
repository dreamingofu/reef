# Reef

A tiny companion for [Navidrome](https://www.navidrome.org/) that runs inside Termux on an Android phone.
Search YouTube from your phone's browser, play a result instantly, and tap **+** to keep it:
Reef downloads the track as `.opus` into your Navidrome music folder and triggers a rescan, so it
shows up in any Subsonic client (Amperfy, Symfonium, play:Sub, ...).

Single Python process, no database, no Docker. Pure-Python dependencies only, so `pip` works in Termux.

## Install (Termux)

Prerequisites: Navidrome already running in Termux, `termux-setup-storage` done, Termux:Boot installed.

```bash
curl -fsSL https://raw.githubusercontent.com/dreamingofu/reef/main/termux/install.sh | bash
```

The script installs `python ffmpeg git`, clones this repo to `~/reef`, installs the Python deps,
asks for your Navidrome admin login (used only to trigger rescans), installs a Termux:Boot script,
and starts Reef on port **5274**.

Open `http://<phone-tailscale-ip>:5274` on your other devices. On iPhone, Share → *Add to Home Screen* makes it behave like an app.

To update later: re-run the same command (it does `git pull` and restarts).

## Config

`~/reef/reef.env` (see `reef.env.example`):

| Key | Default | Meaning |
|---|---|---|
| `REEF_PORT` | `5274` | Port to listen on |
| `REEF_MUSIC_DIR` | `~/storage/shared/Music` | Navidrome's music folder; downloads go to `<dir>/Reef/` |
| `REEF_NAVIDROME_URL` | `http://127.0.0.1:4533` | Navidrome base URL |
| `REEF_NAVIDROME_USER` / `_PASS` | | Navidrome admin login for `startScan` |
| `REEF_PASSWORD` | empty | If set, Reef asks for this password (HTTP Basic) |
| `REEF_AUDIO_FORMAT` | `opus` | Any ffmpeg audio codec yt-dlp accepts (`opus`, `mp3`, `flac`, `m4a`) |

## How it works

- **Search** — `yt-dlp` `ytsearch` with flat extraction (no page fetch per result).
- **Play** — Reef resolves the best audio URL (m4a preferred, which iOS Safari plays natively) and
  proxies it with `Range` passthrough so seeking works. Proxying is required: Google's media URLs are
  bound to the IP that resolved them, so a phone on Tailscale can't fetch them directly.
- **Keep** — a background worker runs `yt-dlp` with `FFmpegExtractAudio` (YouTube's best audio is
  usually already Opus, so this is a remux, not a re-encode), writes tags and embeds the thumbnail,
  then calls Navidrome's Subsonic `startScan`. Temp files stay in `~/reef/tmp`; only the finished
  file is moved to shared storage.

## API

| Route | |
|---|---|
| `GET /api/search?q=` | top results |
| `GET /api/stream/<id>` | proxied audio |
| `POST /api/download` `{id,title}` | enqueue |
| `GET /api/jobs` · `DELETE /api/jobs/<id>` | job list / dismiss |
| `GET /api/health` | Reef + Navidrome status |

## Android gotchas

- **Nothing downloads / "Permission denied"** — Termux lost shared-storage access. Run `termux-setup-storage` again, or Android Settings → Apps → Termux → Permissions → Files and media → Allow.
- **Reef or Navidrome die in the background** — on Android 12+, disable the phantom process killer: `adb shell settings put global settings_enable_monitor_phantom_procs false`. Also exempt Termux and Termux:Boot from battery optimization. The boot script holds `termux-wake-lock`.
- **Search/play suddenly fails** — YouTube changed something; update yt-dlp: `pip install -U yt-dlp` then re-run `~/.termux/boot/reef.sh`.
- **`pip install` is slow** — Termux builds some wheels from source. It only happens once.

## Run elsewhere

Works on any Linux/macOS/Windows box with Python 3.10+ and ffmpeg:

```bash
pip install -r requirements.txt
cp reef.env.example reef.env   # edit
python app.py
```

MIT licensed.
