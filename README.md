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
| `REEF_DEFAULT_SOURCE` | `yt` | Search source when the client sends no `src`: `yt`, `mc` or `nd` (see [Sources](#sources)) |
| `REEF_TRACKS_API` | `https://tracks.monochrome.st` | Monochrome tracks API instance(s), comma-separated, tried with failover; empty disables the `mc` source |
| `REEF_PIPED_INSTANCES` | empty | Piped API instance(s), comma-separated: fallback for YouTube search/stream when yt-dlp fails (see [Sources](#sources)) |
| `REEF_INVIDIOUS_INSTANCES` | empty | Invidious API instance(s), comma-separated: same fallback role, tried after Piped; streams are proxied by the instance (`local=true`) |
| `REEF_HISTORY_FILE` | `~/reef/history.json` | Where kept tracks are recorded |
| `REEF_YT_CLIENT` | empty | yt-dlp YouTube player client list for streaming (e.g. `ios,web_safari`); empty = yt-dlp default. `python scripts/bench_resolve.py <video id>` times each candidate; a candidate must pick an audio-only format (m4a `140`) to be usable |

## Sources

The search screen has a chip per enabled source (hidden when only YouTube is on). Track ids are
`<key>:<id>` for non-YouTube sources; YouTube ids stay bare, so old history and bookmarks keep working.

| Key | Chip | Enabled when | Plays | Keep (+) |
|---|---|---|---|---|
| `yt` | YouTube | always | m4a/AAC via yt-dlp | yt-dlp → `REEF_AUDIO_FORMAT` |
| `mc` | Monochrome | `REEF_TRACKS_API` non-empty | lossless FLAC from the tracks API | download, ffmpeg → `REEF_AUDIO_FORMAT` (copy when it is already that), tags + cover via mutagen |
| `nd` | Library | Navidrome login set | the original file (`stream?format=raw`) | already in the library |

YouTube can fall back to Piped and then Invidious (`REEF_PIPED_INSTANCES`, `REEF_INVIDIOUS_INSTANCES`): they are
used only when yt-dlp fails, are off by default, and need instances that allow API access (many public ones don't).

Sources live in `sources/` (one module each, registered in `sources/__init__.py`).

## How it works

- **Search** — `yt-dlp` `ytsearch` with flat extraction (no page fetch per result).
- **Play** — Reef resolves the best audio URL (m4a preferred, which iOS Safari plays natively) and
  proxies it with `Range` passthrough so seeking works. Proxying is required: Google's media URLs are
  bound to the IP that resolved them, so a phone on Tailscale can't fetch them directly.
- **Keep** — a background worker runs `yt-dlp` with `FFmpegExtractAudio` (YouTube's best audio is
  usually already Opus, so this is a remux, not a re-encode), writes tags and embeds the thumbnail,
  then calls Navidrome's Subsonic `startScan`. Temp files stay in `~/reef/tmp`; only the finished
  file is moved to shared storage.
- **Artwork** — `/api/art/<id>` serves a kept track's Navidrome cover once Navidrome has scanned it
  (looked up by title + file name, then remembered in `history.json`), and otherwise redirects to
  YouTube's thumbnail.
- **Tiles** — the home "moods & genres" tiles are defined in `tiles.py`. A background thread searches
  each tile's query 5 s after start and then daily, caching a few result ids in `tiles.json` so the
  tiles show real artwork without searching on every page load.

## API

| Route | |
|---|---|
| `GET /api/search?q=&src=` | top results from source `src` (`yt`, `mc`, `nd`; default `REEF_DEFAULT_SOURCE`) |
| `GET /api/sources` | enabled sources `[{key,name,can_keep,codec_label,default}]` |
| `GET /api/stream/<id>` | proxied audio (`<id>` is a bare YouTube id or `mc:…` / `nd:…`) |
| `POST /api/prefetch` `{ids}` | warm stream URLs for up to 5 ids (202) |
| `POST /api/download` `{id,title,channel?}` | enqueue (400 for sources that can't keep, e.g. `nd`) |
| `GET /api/jobs` · `DELETE /api/jobs/<id>` | job list / dismiss |
| `GET /api/art/<id>?size=` | track artwork (64-1000 px): Navidrome cover if kept and scanned, else the source's art (302 to YouTube's thumbnail / Monochrome's artwork, Navidrome cover bytes); 404 when there is none |
| `GET /api/tiles` | home tiles `[{label,color,query,ids}]`; `ids` is empty until the first refresh |
| `GET /api/health` | Reef + Navidrome status, `yt_client`, `yt_backend` (which of yt-dlp / piped / invidious last answered search and resolve), `yt_fallbacks`, last `stream_timing`, enabled `sources` |

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
