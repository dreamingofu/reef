# Reef — handoff notes (resume on another machine)

Last updated: 2026-10-21.

## What's running

| Piece | Where | Port | Notes |
|---|---|---|---|
| Navidrome v0.64.2 | Android phone, Termux (`~/navidrome`, data in `~/nd`) | 4533 | Music folder `~/storage/shared/Music`, scan every 1 min, admin user `marianne` |
| Reef (this repo) | Android phone, Termux (`~/reef`) | 5274 | Flask app: YouTube search, play, **+** keeps `.opus` into `Music/Reef/`, then triggers a Navidrome rescan |
| Amperfy | iPhone | — | Subsonic server `http://100.78.71.110:4533` |
| Reef web UI | iPhone Safari / Home Screen app | — | `http://100.78.71.110:5274` |

- Phone: ARM64, Android 16, Termux + Termux:Boot from F-Droid. Tailscale IP `100.78.71.110`.
- Autostart: `~/.termux/boot/` has a Navidrome script and `reef.sh` (copied from `termux/reef-boot.sh`). A reboot test passed: both came back on their own.
- Already done over ADB: phantom process killer disabled; Termux and Termux:Boot exempt from battery optimization.
- Reef config on the phone: `~/reef/reef.env` (it holds the Navidrome password and is git-ignored).
- Transcoding: Navidrome's built-in "opus audio" profile works. It overshoots the requested bitrate by ~25%, so set Amperfy's cellular bitrate to 96–112 to get a real ~128 kbps.

## Updating the phone after changing code

1. Commit and push to `main`.
2. In Termux on the phone, run `cd ~/reef && git pull --ff-only && ~/.termux/boot/reef.sh`
   (or re-run `termux/install.sh`, which also pulls and restarts).

### Driving the phone from a Mac (no typing on the phone)

```bash
brew install android-platform-tools      # adb
adb devices                              # USB, or: Developer options > Wireless debugging > adb pair / adb connect
```

`adb shell` can't enter Termux's environment (`run-as` fails because F-Droid Termux isn't debuggable). Instead, bring Termux to
the front and type into it. Send the output to shared storage so you can read it back:

```bash
adb shell am start -n com.termux/.app.TermuxActivity
adb shell "input text 'cd ~/reef; git pull --ff-only > /sdcard/navidrome_setup/update.log 2>&1; ~/.termux/boot/reef.sh'"
adb shell input keyevent 66              # Enter
adb shell cat /sdcard/navidrome_setup/update.log
```

To reach the phone's services from the Mac: `adb forward tcp:5274 tcp:5274` (Reef) and `adb forward tcp:4533 tcp:4533` (Navidrome).
Then open `http://127.0.0.1:5274/api/health`. Or skip adb and use the Tailscale IP if the Mac is on the tailnet.

## Local development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt          # also needs ffmpeg for downloads: brew install ffmpeg
REEF_PORT=5300 REEF_MUSIC_DIR=/tmp/reef-music \
REEF_NAVIDROME_URL=http://127.0.0.1:4533 REEF_NAVIDROME_USER=marianne REEF_NAVIDROME_PASS=... \
python app.py
```

Pass the Navidrome password through the environment. Don't put it in a committed file: this repo is public.

## Code map

- `app.py`: routes (`/api/search?q=&src=`, `/api/sources`, `/api/stream/<id>` with Range passthrough, `/api/prefetch`,
  `/api/download`, `/api/jobs`, `/api/history`, `/api/library/recent`, `/api/cover/<id>`, `/api/art/<id>`, `/api/tiles`, `/api/health`,
  `/api/suggest?q=`, `/api/radio/<id>`, `/api/link?url=` (pasted YouTube / YT Music links, parsed never fetched), `/share` (Web Share Target → `/?link=…#search`)).
  Track ids: bare 11-char = YouTube, `mc:<id>` = Monochrome, `nd:<id>` = Navidrome library (`sources.parse`).
- `ytmusic.py`: anonymous YouTube Music via `ytmusicapi` (one locked client, 5/10 s timeouts): songs search with artists/album/square
  art, suggestions (10 min cache), radio (30 min cache), song/playlist/album lookups for links, and a 5000-entry videoId → square art
  map that `/api/art` uses. `REEF_YTMUSIC=0` turns it off; a missing package also just disables it.
- `sources/`: one adapter per source (`youtube.py`: search = YT Music → yt-dlp → Piped/Invidious; stream = yt-dlp + fallbacks, `monochrome.py`,
  `navidrome_src.py`, `piped.py`, `invidious.py`), `failover.py` rotates across instance lists, `__init__.py` is the registry.
- `search.py`: yt-dlp flat search (one shared `YoutubeDL`, video ids only). `streamer.py`: source-agnostic resolve cache
  (1 h TTL, concurrent calls for one id share one extraction, 2-thread prefetch pool that yields to the player), keep-alive
  `requests.Session`, Range proxy, per-request timing log line (`stream id=… cache=hit|wait|miss resolve_ms=… ttfb_ms=…`).
- `downloader.py`: in-memory job queue + worker. YouTube: yt-dlp → Opus, tags, cover, blank description. Other sources:
  fetch → ffmpeg → mutagen tags + cover. Both → `history.add` → Navidrome rescan → try to record the Navidrome cover id.
- `history.py`: `history.json` (git-ignored); entries gain `nd_cover`/`nd_id` once Navidrome has scanned the file.
  `navidrome.py`: Subsonic token auth, ping, startScan, newest albums, cover art, `find_song`.
- `tiles.py`: the home mood/genre tiles + a daily background search that caches 4 video ids per tile in `tiles.json`.
- `scripts/bench_resolve.py VIDEO_ID`: times yt-dlp resolution per player client, to pick `REEF_YT_CLIENT` on the phone.
- `templates/index.html` + `static/style.css` + `static/app.js`: the mobile UI, vanilla JS, no build step.

## UI (Octave-style redesign, 2026-10-21)

The layout is modelled on music.octavestreaming.com's mobile app, built from scratch with no copied assets. It has a black background,
pink accent `#fb2c5a`, frosted-glass floating nav pill (Home / Kept / Downloads) with a round search button, and a glass
mini-player that opens a full-screen Now Playing sheet (blurred artwork background, swipe down to close). Home shows a greeting, a
For you / Library toggle, a "Jump back in" poster shelf, Recently kept, Recent searches, and genre tiles. Search shows a Top
result card, Songs rows with **+**, and filter chips. Design tokens are at the top of `static/style.css`.

## Round 2 (2026-10-21): thumbnails, latency, sources

- Now Playing art is sized from the available height (`container-type: size` + `100cqh`), so it no longer bleeds into
  the title row on short phones. "Jump back in" posters are square art with the text below, not an overlay.
- Artwork goes through `/api/art/<id>`: Navidrome cover for kept + scanned tracks, else YouTube/Monochrome art.
  Mood/genre tiles show the top result's thumbnail from `tiles.json`.
- Play latency: the cold path is still one yt-dlp extraction (2-10 s on the phone). Everything else is now warm:
  search prefetches the top 3, tapping prefetches the tapped id, the next queue item is resolved once playback starts,
  one keep-alive connection serves Safari's Range requests, and a spinner shows while loading. Cached starts are < 50 ms
  on the Mac. On the phone run `python scripts/bench_resolve.py <id>` and set `REEF_YT_CLIENT` if a client is clearly faster
  (on the Mac only the default produced an audio-only m4a).
- Sources: YouTube, Monochrome (`tracks.monochrome.st`, FLAC, keepable), Navidrome library (plays the original file).
  Piped/Invidious are YouTube fallbacks, off unless instances are configured; public ones were mostly dead on 2026-10-21.
- `docs/INTEGRATION_LIST.md` (untracked) was produced by another session from a survey of five similar apps; not part of this round.

## Round 3 (2026-10-21): tests + keep pipeline (docs/INTEGRATION_LIST.md Phase 0/1)

- Tests: `pip install -r requirements-dev.txt && python -m pytest -q` (offline). Live: `python scripts/smoke.py`
  (search → resolve → 64 KiB fetch) and `scripts/smoke.py --keep ID ...` (keeps synchronously, prints ffprobe tags
  and whether the cover is square; point REEF_MUSIC_DIR / REEF_HISTORY_FILE / REEF_TMP_DIR at a scratch dir).
- Keeps now tag artist (no "- Topic"/VEVO), album, album artist, year, track number and a cleaned title (filename
  unchanged), embed a center-cropped square JPEG cover, retry with backoff, and 409 on duplicates (`force` re-keeps).
- New `reef.env` keys: `REEF_SPONSORBLOCK=1` (cut non-music segments, off by default), `REEF_YDL_EXTRA` (JSON yt-dlp options).
- iOS fix: YouTube m4a is fragmented MP4 with full durations in moov; iOS added them to the fragments (2x length,
  no `ended`). `streamer.fix_fragmented_durations` zeroes them in the proxied head.

## Round 4 (2026-10-21): lyrics + loudness (Phase 2)

- Keeps measure loudness (ffmpeg ebur128) and write REPLAYGAIN_TRACK_GAIN/PEAK (+ R128_TRACK_GAIN on Opus), then
  embed synced lyrics from LRCLIB (`lyrics.py`, cache in `lyrics.json` next to history.json; misses retried after 24 h).
  `REEF_REPLAYGAIN=0` / `REEF_LRC_SIDECAR=1` toggle them. Title/artist cleanup lives in `tagging.py`.
- `/api/lyrics/<id>`: Navidrome `getLyricsBySongId` for library tracks, else LRCLIB. Now Playing has a Lyrics button
  (bottom row): synced highlight, tap a line to seek, auto-scroll pauses 4 s after you scroll.

## Round 5 (2026-10-21): player (Phase 3)

- Queue logic is pure functions between `// ==== queue-core:start/end ====` in `static/app.js` (shuffle only
  upcoming + restore original order, Play next stays next on unshuffle, dedupe-move, remove/jump/clear).
- ⋯ on every row → Play next / Add to queue / Keep / Share. Now Playing bottom row: Lyrics, Keep, Up next, Sleep, Downloads.
- Sleep timer (15/30/45/60 min, end of track) runs off a deadline checked every 5 s + on timeupdate/visibilitychange.
- `reef.session` in localStorage restores the queue paused at the saved position after a reload.
- Not yet confirmed on a real iPhone: sleep timer firing while locked, sheet drag-to-close.

## Round 6 (2026-10-21): YouTube Music discovery (Phase 4)

- Search uses YouTube Music (`ytmusic.py`, `REEF_YTMUSIC=1`) with yt-dlp as the fallback; rows show artist · album,
  an E badge and square art. Suggestions while typing (`/api/suggest`).
- Paste a YouTube / YT Music link (track, playlist, album) into search (`/api/link`); Android share sheet → `/share`.
- ⋯ → Start radio; Autoplay (Up next toggle, default on) appends ~20 similar tracks when ≤2 remain (`/api/radio`).
- **Phone update now needs pip**: `cd ~/reef && git pull --ff-only && pip install -r requirements.txt && ~/.termux/boot/reef.sh`.

## Round 7 (2026-10-21): browse (Phase 4b)

- Home tiles are YouTube Music moods & genres (`/api/moods`, cached 7 days in `browse.json`; the hard-coded
  `tiles.py` list is the fallback). Pages on the hash: `#moods`, `#mood/<params>`, `#playlist/<id>`, `#album/<id>`,
  `#artist/<id>` (`/api/mood`, `/api/playlist`, `/api/album`, `/api/artist`).
- Album pages swap official-video ids for the album's audio-track ids (OLAK5uy_ playlist, title-checked), and
  **Keep album** (`/api/download/batch`) writes album / album artist / track number / year so Navidrome groups it.
- Hidden artists: `blocklist.json` (`/api/blocklist`), filtered server-side from search, radio, playlists, tiles;
  managed from ⋯ → Hide artist, the artist page, and the status sheet.
- Tap an artist name in any row (or in Now Playing) to open the artist page.

## Production plan (2026-10-21)

`docs/PRODUCTION_PLAN.md`: what's left for production readiness, from a read of seven more apps (Metrolist, ArchiveTune,
Flow, Meld, Gyawun, Musify, BloomeeTunes) plus an audit of Reef. P0 = player error recovery, stream URL lifetime and
client gating, mid-stream resume, typed failures, failover cooldowns, waitress + supervisor + logs, CI.
On-device test of `580a39e` (2026-10-21): 44 offline tests pass in Termux; yt-dlp default client resolves audio-only
m4a in 1.2 s (`android_vr` gives muxed format 18; `ios`, `web_safari`, `tv`, `mweb` fail), so leave `REEF_YT_CLIENT` empty.

## UI remake plan (2026-10-21)

`docs/UI_REMAKE_PLAN.md`: one combined design from the same seven apps' UIs, with specs per component and a build
order. Headlines: cover-coloured mini player with progress ring and swipe-to-skip, Now Playing with background styles,
stretchy transport, joined action row and pull-up queue, depth lyrics, a Library hub replacing Kept/Downloads, new
Settings / Stats / Recap screens. Prerequisite: serve Reef over HTTPS with `tailscale serve`.

## Ideas / next steps

- Check round 2 on the iPhone: Now Playing on the small screen, tile artwork, source chips, FLAC playback over cellular.
- Run `scripts/bench_resolve.py` on the phone and record the numbers here.
- Save the playback queue across reloads; add a lyrics view.
- Optional `REEF_PASSWORD` in `reef.env` if Reef ever gets exposed beyond the tailnet.
