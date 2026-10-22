# Reef — handoff notes (resume on another machine)

Last updated: 2026-10-22.

## What's running

| Piece | Where | Port | Notes |
|---|---|---|---|
| Navidrome v0.64.2 | Android phone, Termux (`~/navidrome`, data in `~/nd`) | 4533 | Music folder `~/storage/shared/Music`, scan every 1 min, admin user `marianne` |
| Reef (this repo) | Android phone, Termux (`~/reef`) | 5274 | Flask app: YouTube search, play, **+** keeps `.opus` into `Music/Reef/`, then triggers a Navidrome rescan |
| Amperfy | iPhone | — | Subsonic server `http://100.78.71.110:4533` |
| Reef web UI | iPhone Safari / Home Screen app | — | `http://100.78.71.110:5274` |

- Phone: ARM64, Android 16, Termux + Termux:Boot from F-Droid. Tailscale IP `100.78.71.110`.
- Autostart: `~/.termux/boot/` has a Navidrome script and `reef.sh` (copied from `termux/reef-boot.sh`). A reboot test passed: both came back on their own.
  Since Round 9 `reef.sh` starts `termux/reef-run.sh`, which restarts Reef after a crash (see Round 9).
- Already done over ADB: phantom process killer disabled; Termux and Termux:Boot exempt from battery optimization.
- Reef config on the phone: `~/reef/reef.env` (it holds the Navidrome password and is git-ignored).
- Transcoding: Navidrome's built-in "opus audio" profile works. It overshoots the requested bitrate by ~25%, so set Amperfy's cellular bitrate to 96–112 to get a real ~128 kbps.

## Updating the phone after changing code

1. Commit and push to `main`.
2. In Termux on the phone, run `bash ~/reef/termux/update.sh` (since Round 10; the first time, `cd ~/reef && git pull
   --ff-only` first so the script exists). It pulls, installs requirements, re-copies the boot script, restarts, waits
   up to 30 s for `/api/health` with the new commit in `/api/diagnostics`, and prints `UPDATE_OK <sha>`; otherwise it
   rolls back to the previous commit, restarts that and prints `UPDATE_FAILED <reason>`. `--log FILE` keeps a copy
   (e.g. `--log /sdcard/navidrome_setup/update.log` to read it over adb), `--dry-run` only prints the steps.

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
python app.py                            # waitress; REEF_DEV=1 for Flask's dev server
```

Tests and lint as CI runs them: `pip install -r requirements-dev.txt && python -m pytest -q && ruff check .`
(`python -m pytest -m live` adds the real-YouTube check; run it on the phone, CI skips it).

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
  The proxy fetches every client range upstream as bounded pieces (2 MiB, then 4 MiB; googlevideo throttles open-ended `bytes=N-` to ~2× realtime), answers with the client's exact range, and logs `stream-body … pieces=…`.
- `downloader.py`: in-memory job queue + worker. YouTube: yt-dlp → Opus, tags, cover, blank description. Other sources:
  fetch → ffmpeg → mutagen tags + cover. Both → `history.add` → Navidrome rescan → try to record the Navidrome cover id.
- `history.py`: `history.json` (git-ignored); entries gain `nd_cover`/`nd_id` once Navidrome has scanned the file.
  Entries with a `file` are never dropped (before Round 10 the file was cut to 500, so older keeps lost their kept
  state); reads use `store.JsonFile`'s cached copy.
  `navidrome.py`: Subsonic token auth, ping, startScan, newest albums, cover art, `find_song`.
- `tiles.py`: the home mood/genre tiles + a daily background search that caches 4 video ids per tile in `tiles.json`.
- `store.py`: `JsonFile` (in-memory copy re-read when the file changes, atomic `.tmp` + `os.replace` writes, a lock
  for read-modify-write) and `clean_track` (the only place client track dicts are trimmed before storing).
  `listens.py` (play log + pure stats/recap aggregates), `likes.py`, `playlists.py` (+ `.m3u`), `settings.py`
  (runtime overrides of env defaults) sit on it; their files default next to history.json (Round 10).
- `log.py`: logging setup (stderr + `logs/reef.log` rotated 1 MB × 5 + a 300-line in-memory tail), `redact()` for
  signed-URL params / Subsonic tokens / cookies. Modules log through `logging.getLogger(__name__)`, no print().
- `termux/reef-run.sh`: restart loop with backoff; `termux/reef-boot.sh` (Termux:Boot) (re)starts it.
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

## Round 8 (2026-10-22): playback reliability (production plan P0.1-P0.5)

- googlevideo throttles open-ended `Range: bytes=N-` to ~32 KB/s (2× realtime); bounded ranges run at MB/s. The proxy
  now fetches upstream in 2 MiB / 4 MiB pieces while answering the client's exact range: a 44 MB track streams in ~3 s,
  and a 45-min track starts on iOS in ~3 s (before: never reached `loadedmetadata`).
- Stream cache TTL comes from the URL's `expire`; 403s are classified (expiry / client denied / token) and gate that
  client (`player_client` exclusion); same-file URL swap mid-song; mid-stream resume on dropped connections.
- Failures are typed (`errors.py`: BOT_WALL, NETWORK, REGION, GONE, …) with user hints; `POST /api/yt/reset` and a
  status-sheet button reset the YouTube session. Piped/Invidious hosts get cooldowns.
- Player recovery: reload with `?fresh=<token>` and seek back (3 tries, 1/2/4 s), skip unplayable tracks, stop after 3
  dead tracks in a row, resume on `online`. Test hook: `REEF_FAKE_WALL=1`.

## Round 9 (2026-10-22): run it like a service + CI (production plan P0.6, P0.7)

- **Server**: `python app.py` serves with waitress (16 threads, `channel_timeout=120`, `connection_limit=100`, no
  `Server` header, `outbuf_high_watermark` 1 MiB so an aborted Range doesn't pull MBs from googlevideo).
  `REEF_DEV=1` = Flask's dev server. Streaming is unchanged: on the Mac `/api/stream/FGBhQbmPwH8` (5.2 MB) Range 0-
  had a cold TTFB of 1.2-1.5 s (resolve included), warm 21-32 ms, the whole body in ~0.2 s; a 1.3 MB/s reader gets
  the first chunk in 70 ms and 1 MiB per ~0.9 s; a client that stops after 64 KB leaves the proxy at ~1.9 MB pulled.
- **Who can connect**: loopback and Tailscale (`100.64.0.0/10`, `fd7a:115c:a1e0::/48`) need nothing. Any other
  address (home Wi-Fi) gets a 403 ("Reef is only open on Tailscale…") unless `REEF_PASSWORD` is set, then HTTP Basic
  (any user name). Only the socket address counts (`X-Forwarded-For` is ignored). `REEF_OPEN=1` = the old
  behaviour (all networks alike; the password, if set, applies to every client). Careful: `tailscale serve`/`funnel`
  connect from 127.0.0.1, so a **Funnel** (public internet) would be trusted; don't funnel Reef.
- **Supervisor**: `termux/reef-run.sh` runs `python app.py` in a loop, waiting 2, 4, 8 … 60 s between restarts
  (reset after 5 min up). Files: `reef-run.pid` (loop), `reef.pid` (app), `logs/reef-run.log` (restarts),
  `logs/reef.out` (console of the current run, start-up errors only; previous run in `reef.out.1`).
  `~/.termux/boot/reef.sh` takes the wake lock, stops a running loop/app and starts the loop, so running it by hand
  still means "restart". Stop Reef: `kill "$(cat ~/reef/reef-run.pid)"`. termux-services (runit `sv`) would also
  work but isn't needed.
- **Logs**: `~/reef/logs/reef.log` (1 MB × 5, the old ever-growing `reef.log` can be deleted), lines like
  `2026-10-22 01:51:27,969 I stream id=… cache=miss resolve_ms=1328 …`. Signed-URL params (`sig`, `lsig`,
  `signature`, `pot`, `n`), Subsonic `t`/`s`/`p` and Cookie/Authorization values are logged as `len=N,sha1=xxxxxx`.
  Uncaught exceptions in threads land in the log too.
- **Diagnostics**: `GET /api/diagnostics` (versions incl. git sha, uptime, config without secrets, stream cache,
  last stream timing, gates, failover cooldowns, recent jobs, the last 300 log lines). Status sheet → **Copy
  diagnostics** copies it as JSON (fetched when the sheet opens, so the tap copies at once on iOS over http).
- **CI**: `.github/workflows/ci.yml` runs pytest + `ruff check .` on Python 3.12 and 3.13 (ubuntu, ffmpeg from apt)
  for pushes/PRs that touch more than docs. Ruff config (pyflakes + syntax only) and pytest settings are in
  `pyproject.toml`; `-m live` tests are deselected by default and skip on GitHub Actions.

### Deploy Round 9 (on the phone, once)

1. `cd ~/reef && git pull --ff-only && pip install -r requirements.txt` (adds **waitress**).
2. `cp ~/reef/termux/reef-boot.sh ~/.termux/boot/reef.sh && chmod +x ~/.termux/boot/reef.sh` (the new boot script
   starts the supervisor; the old one ran app.py once).
3. `~/.termux/boot/reef.sh` (stops the old app via `reef.pid`, starts the loop), then check
   `curl -s http://127.0.0.1:5274/api/health` and `cat ~/reef/logs/reef-run.log`.
4. Open Reef on the iPhone over Tailscale as before (no password prompt). From home Wi-Fi by LAN IP it now answers
   403; to allow that, put `REEF_PASSWORD=…` in `~/reef/reef.env` and run `~/.termux/boot/reef.sh` again.
5. Optional: `rm ~/reef/reef.log` (the old nohup log); try Status → Copy diagnostics; `python -m pytest -m live`.

## Round 10 (2026-10-22): data foundation (listens, likes, playlists, settings, P1.5, update.sh)

Backend for the UI remake's Library / Stats / Recap / Settings screens (docs/UI_REMAKE_PLAN.md §6, §11, §12); the
screens themselves are a later batch. New files `listens.json`, `likes.json`, `playlists.json`, `settings.json`
(git-ignored; `REEF_LISTENS_FILE` / `REEF_LIKES_FILE` / `REEF_PLAYLISTS_FILE` / `REEF_SETTINGS_FILE`).

- **Listen log**: the player reports every play to `POST /api/listens` (`navigator.sendBeacon`, `fetch keepalive`
  fallback) on track change, natural end, page hidden and pagehide. Played seconds add up timeupdate deltas while
  playing (a seek counts nothing, `L.playedDelta` in the `library-core` section of app.js). The server counts a play
  at >= 30 s or >= 50 %, keeps short skips (`skipped`), keys entries by id + `started_at` (a second report of the same
  play updates it) and keeps 20 000 entries. `/api/stats` (rolling week/month/year/all) and `/api/recap` (closed
  calendar month/year) are pure functions over the log; hours/weekdays/days use the browser's `tz`.
- **Likes** and **local playlists** (CRUD, pin, insert at position, dedupe, move, `.m3u` download, export to
  `<music>/Reef/Playlists/<name>.m3u` + Navidrome rescan so Navidrome imports it). Lists from search, radio, link,
  album, artist, playlist pages carry `liked` next to `kept`.
- **Settings**: `settings.json` overrides a whitelist of env defaults at runtime (`REEF_*` stays the default; PATCH
  null resets). Keeps (SponsorBlock, ReplayGain), the lyrics sidecar, YouTube Music and the default source read
  through it. `listenbrainz_token` is write-only. Diagnostics shows the effective values.
- **P1.5**: lists and `/api/history` expose `nd_id` for kept tracks Navidrome has scanned (filled by the keep or
  lazily by `/api/art`); with `nd` in `/api/sources` and `prefer_library` on (default), the player streams
  `nd:<nd_id>` and the UI keeps the original track. A failing library stream falls back to the original id once.
- **`termux/update.sh`**: see "Updating the phone". Tested in `tests/test_update_sh.py` against throwaway repos.

### API shapes (for the UI batch)

All JSON. Track dicts are `{id, title, channel, duration, source, album?, album_id?, artists?[{name, id}],
explicit?}`; lists from the server add `kept`, `liked`, and `nd_id` when kept + scanned.

- `POST /api/listens` `{id, title, channel, artists?, album?, album_id?, duration, played_s, started_at (epoch s),
  ended_at?, skipped, source, via?: "nd"}` → 201 new / 200 updated `{entry, created}`; entry = track +
  `{played_s, started_at, ended_at, counted, skipped (only when not counted), via?}`. 400 `{error}`.
- `GET /api/listens?limit=50 (1-500)&before=<epoch>&counted=1|0` → entries newest first. `DELETE /api/listens` → 204.
- `GET /api/stats?period=week|month|year|all&tz=<IANA>&utc_offset=<min east>&limit=10` → `{period, from, to,
  listens, minutes, skips, tracks_count, artists_count, days_count, top_tracks[{id, title, channel, duration, source,
  album?, artists?, count, minutes}], top_artists[{name, id, count, minutes}], top_albums[{name, artist, album_id,
  cover_id, count, minutes}], by_hour[24], by_weekday[7] (Mon = 0), streak_days, longest_streak_days,
  first_listen_at, most_skipped[{id, title, channel, count}]}`. Counts are counted plays; minutes include skips.
- `GET /api/recap?period=month|year&offset=0 (last closed; 1 = the one before; -1 = current so far)&tz=` → the
  same aggregates (no `streak_days`) + `{offset, period_label ("September 2026" / "2025"), partial, new_artists_count,
  new_artists[{name, id, count}] (top 5), top_track_play_time (min), longest_session_min (time played in the longest
  run of plays without a 30-min gap)}`.
- `GET /api/likes` → `[track + {liked_at, kept, liked, nd_id?}]` newest first; `PUT /api/likes/<id>` (body: the
  track, optional) → 201/200 `{entry, created}`; `DELETE /api/likes/<id>` → 204 / 404.
- `GET /api/playlists` → `[{id, name, description, pinned, created_at, updated_at, cover_id, count, duration,
  exported?{path, at}}]` pinned first, then most recently changed. `POST /api/playlists {name, description?, tracks?}`
  → 201 playlist. `GET /api/playlists/<id>` → playlist with `tracks` (marked). `PATCH {name?, description?, pinned?}`,
  `DELETE` → 204. `POST /<id>/tracks {tracks, position?, dedupe?}` → playlist + `{added, skipped}`.
  `DELETE /<id>/tracks/<index>?id=<track id>` (409 if another track is there now). `POST /<id>/move {from, to}`.
  `GET /<id>.m3u` (download). `POST /<id>/export` → `{path (relative to the music folder), tracks, skipped,
  rescanned}`, 409 when nothing is kept.
- `GET /api/settings` → `{values: {key: value, listenbrainz_token: {set}}, schema: [{key, type: bool|choice|list|secret,
  default, label, help, choices?, overridden}]}`; `PATCH {key: value|null}` → same, 400 `{error}` (nothing written).
  Keys: default_source, ytmusic, prefer_library, autoplay_radio (default for new devices; not wired in the client
  yet), sponsorblock_keep, replaygain, lrc_sidecar, lyrics_providers (placeholder, not read yet), listenbrainz_token.

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
- A supervisor loop for Navidrome too (same idea as `termux/reef-run.sh`; out of scope for Round 9).
- Change the weak Navidrome admin password (Reef only needs it for `startScan`).
