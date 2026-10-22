# Reef — handoff notes (resume on another machine)

Last updated: 2026-10-22 (Round 16).

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
- `recommend.py` (Round 11): For you from local data: seeds, Quick picks scoring over YT Music radio lanes, artist
  affinity, the shelves from the listen log, and "Not interested" marks (`dislikes.json`). `home.py`: `/api/home`
  served from `home.json`, rebuilt by a background thread (`reef-home-tick`) when due.
- `tiles.py`: the home mood/genre tiles + a daily background search that caches 4 video ids per tile in `tiles.json`.
- `store.py`: `JsonFile` (in-memory copy re-read when the file changes, atomic `.tmp` + `os.replace` writes, a lock
  for read-modify-write) and `clean_track` (the only place client track dicts are trimmed before storing).
  `listens.py` (play log + pure stats/recap aggregates), `likes.py`, `playlists.py` (+ `.m3u`), `settings.py`
  (runtime overrides of env defaults) sit on it; their files default next to history.json (Round 10).
- `scrobble.py` (ListenBrainz queue + worker), `updater.py` (version check, in-app update child), `backup.py`
  (backup / restore / daily snapshots), `health.py` (`/api/health/sources`): Round 12.
- `matching.py` (same-song scoring + `matches.json`), `segments.py` (SponsorBlock at play time): Round 13.
- `lyrics.py` (LRCLIB, the provider chain, cache, offset/pin, embedding) + `lyrics_providers.py` (SimpMusic,
  YouTube Music, BetterLyrics TTML, the text checks): Round 14.
- `personal.py` (opt-in anonymous YouTube Music personalisation), `charts.py` (YouTube Music charts),
  `aliases.py` (artist display names): Round 15.
- `importer.py` (CSV / Google Takeout playlist import + background matching), `itunes.py` (iTunes suggestions
  fallback): Round 16.
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
  yet), sponsorblock_keep, replaygain, lrc_sidecar, lyrics_providers (read since Round 14), listenbrainz_token.

## Round 11 (2026-10-22): For you shelves + Home from cache (production plan P1.10, P1.14)

New files `dislikes.json`, `home.json` (git-ignored; `REEF_DISLIKES_FILE` / `REEF_HOME_FILE`, next to history.json).

- **Quick picks** (`recommend.py`): seeds = newest counted listens (then newest keeps), one per lead artist, max 5,
  YouTube ids only, none disliked/hidden. Each seed's `ytmusic.radio` (30 min cache) is a lane; a track scores
  Σ (1 − i/8)(1 − s/n), where i is its place among the lane's *eligible* tracks (a lane of songs you just heard still
  yields 8), times `max(0.2, 1 + affinity)` of its lead artist. Dropped: heard (any play) in 14 days, kept, disliked,
  hidden artists, the seeds, and a second id of the same title + artist. Best 15, ≤ 3 per lead artist, then
  interleaved lane by lane. A 429 stops the remaining lanes; other errors lose one lane.
- **Affinity**: per artist, oldest → newest: decay ×0.85/day since its last update, then a counted play
  `a += 0.15 (1 − a)`, a skip `a += 0.05 (−1 − a)`; decayed to now.
- **Other shelves**: On repeat (≥ 2 counted plays in 30 days), Jump back in (≥ 2 plays 30-180 days ago, nothing in
  the last 14), Liked songs (latest 10, read per request), Your top artists (90 days, else all time; `cover_id` =
  their most played track for the art; channel id when any listen had it), Mixed for you (a card per seed: `seed`
  track + up to 3 other artists from its lane), Moods (`tiles.load()`, read per request).
- **Not interested**: `POST /api/dislikes/<id>` (body = the track) → 14-day cooldown; a second disliked *track* by
  the same lead artist within 30 days adds the artist to the blocklist (channel id from either mark) and returns it
  as `blocked` (only when it created the block; an artist already hidden stays the user's block). `DELETE` undoes the mark and a block it created. Marks older than 30 days are pruned on write.
- **Home cache** (`home.py`): `GET /api/home` serves `home.json` (~1-5 ms) and filters it per request (hidden
  artists, dislikes). Rebuild due: 6 h after a complete build, 30 min after lost lanes (`radio_failed`), 60 s or
  the 429's `Retry-After` (`ytmusic` now records it with a session response hook; ytmusicapi's exception drops
  headers) after `rate_limited`; also after 5 counted listens *reported* since the build (1 when there were no
  seeds), except during a 429 wait; and when the YouTube Music setting flipped. One build at a time (lock). A build
  that lost every lane keeps the previous Quick picks (`carried`). No file yet: the request waits ≤ 8 s for the build,
  else returns the network-free shelves with `stale: true`, `reason: local_only`. Thread checks every 60 s (first
  after 20 s). `/api/diagnostics` → `home` shows the state.
- **Client**: Home renders /api/home (localStorage `reef.home` paints first, refetch when Home is shown and the copy
  is > 5 min old, or the app comes back to the foreground; a `stale` answer is re-asked up to 3 times, 8/16/24 s).
  Order: Quick picks (4-row columns, Play all), On repeat, Mixed for you, Jump back in (the old device-local
  "recently played" posters until the server has one), Liked songs, Your top artists, Recently kept, Recent
  searches, Moods. ⋯ → **Not interested** (all but `nd:` tracks): drops it at once, toasts, and toasts the hide when
  it escalates. Hiding/unhiding an artist anywhere also drops/refetches Home.
- Measured on the Mac (4 artists of seeded listens, live YT Music): cold `/api/home` 4.9 s (5 radio calls, fresh),
  warm 1-5 ms, rebuild after 5 listens 3.1 s in the background.

### API shapes

- `GET /api/home` → `{generated_at, stale, source: "fresh"|"cache"|"fallback", reason?: "radio_failed"|
  "rate_limited"|"local_only", shelves: [{id, title, kind, items}]}`. ids/kinds: `quick`, `on_repeat`, `jump_back`,
  `liked` = tracks (marked `kept`/`liked`/`nd_id`; `plays` on on_repeat/jump_back); `mixed` = seeds `{name,
  artist_id, seed: track, with: [names]}`; `top_artists` = artists `{name, id, count, cover_id}`; `moods` = tiles.
- `POST /api/dislikes/<id>` → 201/200 `{entry: track + {at, until, artist_key, artist_id, blocked?}, created,
  blocked: blocklist entry|null}`; 400 body id mismatch, 404 bad id. `DELETE` → 204/404. `GET /api/dislikes` → marks
  of the last 30 days, newest first.

## Round 12 (2026-10-22): scrobbling, updates, backup, source health (production plan P1.4, P1.11-P1.13)

New files `scrobbles.json`, `update.json`, `backups/` (git-ignored; `REEF_SCROBBLES_FILE` / `REEF_UPDATE_FILE` /
`REEF_BACKUP_DIR`, next to history.json). Status sheet → four new rows that open sub-sheets (back arrow returns):
**Sources**, **Updates**, **Scrobbling**, **Backup**. The Server sheet now scrolls (`.sheet.tall`).

- **Scrobbling** (`scrobble.py`, ListenBrainz only; Last.fm and `playing_now` skipped: the player reports plays
  only when they end). A counted listen (`POST /api/listens`) with a token set and `scrobble_enabled` on is queued
  (`scrobbles.json` `pending`, cap 2000) and a worker thread (`reef-scrobble`) submits it as `listen_type: single`
  with `track_metadata {artist_name (artists joined, else tagging.guess on the YouTube title/channel), track_name,
  release_name?, additional_info {submission_client, media_player: "Reef", duration_ms?, origin_url + music_service
  for YouTube ids}}`. Offline / 429 / 5xx → queue-wide backoff 30 s doubling to 1 h (Retry-After /
  X-RateLimit-Reset-In honoured); other 4xx → that listen is dropped and logged; **401 keeps the listens** and pauses
  1 h (until a new token validates) instead of dropping them (deviation from "drop on 4xx": a typo shouldn't lose a
  week of scrobbles); anything queued > 14 days is dropped. Submitted keys `<id>@<started_at>` are remembered (last
  1000) and the listen entry gets `scrobbled: true` (kept when a later report of the same play replaces the entry).
  Saving the token (`PATCH /api/settings`) runs `GET /1/validate-token` (≤ 10 s) and stores the user name. The token
  is never logged, returned, in diagnostics or backups (only a sha256 prefix, to tie the user name to it).
- **Update check + in-app update** (`updater.py`). `/api/version` compares `git log -1` with GitHub
  `commits/main` (If-None-Match → free 304s) and, once per new pair, `compare/<local>...<latest>` for the count;
  yt-dlp vs PyPI `info.version` compared numerically (PyPI drops leading zeros: 2026.8.19 = 2026.08.19). Cached in
  `update.json` 6 h (30 min after a failure); a `reef-chores` thread (every 60 s, first after 60 s) refreshes it when
  due and takes the daily backup. `POST /api/update {what, confirm: true}` → 202, the child runs in its own session
  with output in `logs/update.log`: `reef` = `bash termux/update.sh` (unchanged; `REEF_UPDATE_PIP="<python> -m
  pip"`), `ytdlp` = `python updater.py ytdlp` (pip -U, restart via `~/.termux/boot/reef.sh` or termux/reef-boot.sh,
  wait ≤ 30 s for /api/diagnostics to report the new yt-dlp, else reinstall the old one and restart), `both` = the
  yt-dlp step then update.sh. Guards: loopback/Tailscale socket address only (a REEF_PASSWORD client on the LAN gets
  403), `confirm: true`, one run at a time (409), and Termux only (`PREFIX`/`TERMUX_VERSION`/the Termux files dir)
  unless `REEF_ALLOW_SELF_UPDATE=1` — for **both** targets, not just reef (the restart needs the Termux supervisor).
  The run (pid, start) is in `update.json`, so the restarted process still reports it; result = the log's last
  `UPDATE_OK` / `UPDATE_FAILED` line (bare or after a time stamp; re-read on every status call, so a
  restart mid-run can't freeze a wrong result: f484a75 reported a successful yt-dlp update as failed). The client polls `/api/update/status` every 2 s, polls `/api/health` while the
  server is down, toasts the result and reloads the page after a Reef update. "Skip this version" =
  `PATCH /api/settings {skipped_version: <latest sha>}`.
- **Backup / restore** (`backup.py`). `GET /api/backup` builds `{"format": "reef-backup", "schemaVersion": 1,
  createdAt, app: {reef}, sections: {history, lyrics (hits only), listens, likes, playlists, dislikes, blocklist,
  settings}}`, writes it to a temp file, reads it back, validates and serves it as `reef-backup-<date>.json`.
  Settings are an allow-list of preferences (`backup.SETTINGS_KEYS`): never the token, instance URLs or
  `skipped_version`. `POST /api/restore` (multipart `file` + `mode`, or a JSON body + `?mode=`; ≤ 20 MB via
  Flask 3.1's per-request `max_content_length`, the rest of the app keeps 4 MB) refuses another format / newer schema
  / a section of the wrong type, skips + counts bad items, snapshots to `backups/pre-restore-<UTC ts>[-n].json`
  (keep 10), then rewrites each store under its own lock with its atomic write. `replace` swaps each section present
  in the file; `merge` unions by id, strictly newer wins (kept_at / liked_at / at / updated_at; listens by
  (id, started_at), longer play wins; settings: what's set here stays), so a second merge is a no-op. Sections absent
  from the file are untouched. History `file` paths are kept only when the file exists on this device. Daily
  `backups/auto-<ts>.json`, keep 7. Measured on the Mac: a 6.5 MB backup (19 000 listens) restores in ~0.15 s.
- **Source health** (`health.py`). `GET /api/health/sources` runs every check in parallel (6 s each, a check that
  doesn't answer is OFFLINE), cached 60 s (`?refresh=1` re-runs). YouTube = a stream that played in the last 10 min,
  else `streamer.resolve(dQw4w9WgXcQ)` (shares the stream cache); YouTube Music = an uncached suggestions call;
  each Monochrome / Piped / Invidious instance (search JSON); Navidrome ping; LRCLIB `/api/get`; ListenBrainz
  validate-token (with a token); GitHub / PyPI HEAD. Its own requests session, so checks never cool failover hosts.
  On the Mac: YouTube 1.3 s cold, the whole panel ~1.3 s, cached 11 ms.
- **Instance lists in Settings**: `tracks_api`, `piped_instances`, `invidious_instances` (type `urls`:
  comma-separated string or list, http(s) only, ≤ 10; blank = the reef.env list). Monochrome and an enabled
  Piped/Invidious follow a change at once; turning Piped/Invidious on from an empty env list takes a restart
  (fallbacks register at start). New settings types `text` (skipped_version) and `urls`; new bool `scrobble_enabled`.

### API shapes

- `GET /api/scrobble/status` → `{enabled, token_set, scrobble_enabled, pending, submitted, last_ok_at, last_error,
  last_error_at, retry_at, user_name, token_valid (true|false|null = not checked / unreachable), validated_at}`.
  `POST /api/scrobble/validate` → the same after asking ListenBrainz.
- `GET /api/version[?refresh=1]` → `{reef: {sha, short, date (epoch)}, latest_reef: {sha, short, date (ISO),
  message}|null, behind: bool|null (null = GitHub doesn't know this commit), behind_count, ytdlp, latest_ytdlp,
  ytdlp_outdated, checked_at, skipped_version, skipped, errors: [str], self_update: {allowed, reason}}`.
- `POST /api/update {what: reef|ytdlp|both, confirm: true}` → 202 status | 400 / 403 / 409 `{error}`.
  `GET /api/update/status` → `{running, what, started_at, finished_at, result: ok|failed|null, log_tail: [≤ 20
  redacted lines]}`.
- `GET /api/backup` → the file. `GET /api/backup/status` → `{last_auto_at, snapshots: [{name, kind: auto|pre-restore,
  at, size}], max_bytes, sections}`. `POST /api/restore` → `{mode, snapshot, sections: {name: {before, in_backup,
  skipped, after}}}` | 400 / 413 `{error}`.
- `GET /api/health/sources[?refresh=1]` → `[{name, kind: youtube|ytmusic|monochrome|piped|invidious|navidrome|
  lyrics|scrobble|updates, status: ONLINE|REACHABLE|OFFLINE|DISABLED, latency_ms, detail, checked_at}]`.

### To check on the phone

1. Deploy as usual (`bash ~/reef/termux/update.sh`); no new packages.
2. Status → Updates should show this commit; after the next push, "Update Reef" end to end: confirm, the log tail
   moves, the page goes quiet during the restart, then "Reef updated" and the page reloads on the new commit
   (`logs/update.log` has the run). Also "Update yt-dlp" (restart + /api/diagnostics shows the new version).
   Check that `is_termux()` is true under Termux:Boot (`PREFIX` set) — else the buttons say "only on Termux".
3. Scrobbling: paste the real token → "Scrobbling as <user>"; play a song > 30 s, it shows on listenbrainz.org
   within a minute; airplane mode → "Waiting to send" grows, then drains when back online.
4. Backup: Download on iPhone Safari (saves to Files), then Restore from that file with Merge; check
   `~/reef/backups/` gets `auto-*.json` daily.
5. Sources: the panel on cellular vs Wi-Fi, and how long the YouTube row takes on the phone.

## Round 13 (2026-10-22): matching, lazy queue + dead-track recovery, SponsorBlock at play time, lossless keep (production plan P1.6-P1.9)

New file `matches.json` (git-ignored; `REEF_MATCHES_FILE`, next to history.json). New settings `keep_lossless`
(default `REEF_KEEP_LOSSLESS`, off), `sponsorblock_play` (on), `replace_dead_tracks` (on), all three in
`backup.SETTINGS_KEYS`. `matches.json` (incl. manual fixes) isn't in backups yet.

- **Matching** (`matching.py`). Normalised for comparison only: lowercase, accents folded for mostly-Latin text,
  no `(feat. X)` / `ft. X` / `[...]` / `(... remaster|remix|<version word> ...)` / ` - Remastered 2011` / video noise
  (`tagging._strip_noise`), no punctuation; artist = the first of `A, B & C feat. D` (also ` x `, `and`, `with`),
  no " - Topic"/VEVO, compared without spaces. Raw score = 0.45 Dice(title) + 0.35 Dice(artist) + 0.20 duration
  (1.0 ≤ 2 s, 0.8 ≤ 5 s, 0.5 ≤ 10 s, 0.2 ≤ 30 s, else 0; unknown 0.5; an unknown artist also counts 0.5). Version
  words (live, acoustic, remix, sped up, nightcore, slowed, instrumental, karaoke, cover, demo, 8d, music/lyric
  video) on one side only: −0.15 each, cap −0.30, ranking only. `best()` accepts raw ≥ 0.35 and stops at a
  *ranked* ≥ 0.95 (so a remix with a perfect raw score never ends the scan before the original). **Title floor** on
  top of the raw floor (normalised-title Dice): ≥ 0.6 when both artists are known and alike (artist Dice ≥ 0.5),
  ≥ 0.75 when either is unknown, ≥ 0.9 when they differ; a made-up title no longer matches an unrelated song on
  artist-name likeness, "Мечтать" no longer matches the same artist's "Мечтой" (0.55), "Hello" (Adele) never
  "Help!" (Beatles). The lossless scorer applies the same floor unless the ISRC matches. `best(same_artist=True)`
  (used by `/api/replace`) also rejects a known artist unlike the query's. `query_of(track)`
  splits "Artist - Song (Official Video)" YouTube titles without an artist list like a keep tags them.
  Cache: key → `{id, score, manual, at, track}` (the slim track, so a hit answers without a search); automatic
  entries are re-checked after 30 days, manual ones never expire and are never overwritten; 5000 entries max
  (oldest automatic first). Lyrics keep their own LRCLIB `_pick` (duration-gated ranking of LRCLIB's own hits;
  matching.py wouldn't simplify it).
- **Lazy queue** (P1.8). Queue entries may be placeholders `{id: "pq:…", q: true, title, artist, duration, album?,
  key?}` (no producer yet: imports / charts later). `match-core` (`M` in app.js) has the pure parts. Within 2 of
  the current track (and before one plays) the client asks `/api/match` and swaps the song in place (queue + shuffle
  snapshot; a song already in the queue drops the entry instead, ids stay unique). 404 → dropped, toast "Skipped
  “…”: not found"; a failed lookup is retried after 30 s, or skipped ("Couldn't look up …") when it's due to play.
  A restored session that stopped on a placeholder looks it up on play.
- **Dead-track recovery** (P1.8). When recovery gives up on a YouTube / Monochrome track with block GONE, REGION
  or MEMBERS_OR_PAID (and the user wanted playback, `replace_dead_tracks` on, the track isn't itself a replacement),
  the client asks `POST /api/replace`: YouTube Music songs (title + artist, then the title alone), Monochrome too when
  YouTube had nothing ≥ 0.93; accept ≥ 0.45; the same artist when both are known (same title by someone else is a
  cover or another song); never the dead id or earlier dead ids (`exclude`). The replacement
  takes the same queue position (`replaced_from` marks it, saved in the session), toast "Played a replacement for
  “…”"; none → the old skip path. History, likes and playlists keep the original id (nothing rewritten silently).
- **SponsorBlock at play time** (P1.9, `segments.py`). `GET https://sponsor.ajay.app/api/skipSegments/<sha256(id)[:4]>
  ?categories=["music_offtopic"]&actionTypes=["skip"]&service=YouTube`, our `videoID` picked from the bucket,
  segments < 1 s dropped. Memory LRU (2000): results and "none" (404 / not in the bucket) 7 days, errors 5 min
  (502 meanwhile). Client: fetched on load for YouTube tracks (`sponsorblock_play`), never when the stream is the
  library copy (`viaLibrary()`); on `timeupdate` jumps to each segment's end once (not inside its last 0.5 s, not
  while recovery holds the playhead: `rec.busy/broken/seekTo`, `resume`, and the list is tied to `rec.token`);
  a user seek (`seekTo`: bar, lyrics, lock screen) into a segment plays it. Toast "Skipped non-music part" once per
  track.
- **Lossless upgrade on keep** (P1.7, `keep_lossless`). Before a YouTube keep: Monochrome searches in order ISRC (only
  when a hint carries one: no YouTube metadata Reef reads has ISRCs), title + artist + album, title + artist, title.
  Points = 500 × matching's raw score (titles compared without `(Radio Edit)`-style soft groups), so Meld's minimum
  330 = raw 0.66; ISRC +1000; remix/live/instrumental/karaoke/sped up/slowed on one side → rejected; remaster/edit/
  acoustic/deluxe on one side (title + album) −45 each; |Δduration| > 25 s → rejected. The FLAC is kept as is (no
  transcode) through the generic keep path with the job's hints; a download shorter than half the listed length
  (a preview) or any failure *before the file reaches the music folder* → silently the normal YouTube keep (a
  half-copied `.part` is deleted first). Once the FLAC is in place the keep counts as done: a failed history write
  is a `warning` on the job (shown under it in Downloads), and any other error fails the job; never a YouTube keep
  too (Navidrome would list the song twice). The same `.part` cleanup and history `warning` apply to every generic
  (Monochrome) keep. History: entry under the `mc:` id with
  `upgraded_from: <yt id>`; `_mark_kept` and the keep duplicate guard (409) treat the YouTube id as kept too, so
  P1.5 streams it from Navidrome. Jobs carry `lossless`, `upgraded_to`, `duration` (the client now sends it); the
  Downloads tab shows a LOSSLESS badge and "Saving FLAC…". Log lines `keep-lossless id=… result=ok|no-match|
  fallback|search-failed`.

### API shapes

- `POST /api/match {title, artist?, duration?, album?, key?}` → `{track (marked kept/liked), score, key, manual,
  cached}`; 404 `{error, key}`; 400 bad input; 502 YouTube Music off / failing. `key` defaults to
  `q:<norm artist>|<norm title>`.
- `PUT /api/match/<key> {id}`: `id` = a pasted YouTube / YT Music link or video id (`parse_link`), or an `mc:`/`nd:`
  id → `{track, score: null, key, manual: true, cached: false}`; 400 not a song link, 404 bad key.
- `POST /api/replace {track, exclude?: [ids], key?}` → `{track, score, source: yt|mc, replaced}`; 404 none, 400 bad
  track / `nd:` track, 502 when every source asked failed. A valid `key` re-points that match (unless manual).
- `GET /api/segments/<video id>` → `[{start, end, category}]` (`[]` none or setting off); 404 non-YouTube id, 502
  SponsorBlock unavailable.
- `POST /api/download` also takes `duration` (seconds); jobs have `duration`, `lossless`, `upgraded_to`.

### Measured on the Mac (live, port 5308, scratch data)

`/api/match`: Despacito / Luis Fonsi → `FXovf5dsRTw` 1.0 (1.3 s); "Bohemian Rhapsody - Remastered 2011" / Queen →
1.0; Around the World / "Daft Punk - Topic" (no length) → 0.9; Hello / Adele 1.0; Help! → "Help! (Remastered
2009)" 0.9; 夜に駆ける / YOASOBI 0.9; a made-up title → 404 (0.41 before the title floor). Мечтать / Земфира: YT Music
has no Zemfira upload of it; before the floor it matched her "Мечтой" (0.70), now the same title by Владимир Клявин
(0.59; a plain match allows another artist at title ≥ 0.9), while `/api/replace` gives 404. `/api/replace`: the audio id
excluded → another upload 1.0 (0.5 s); a fake VEVO-style id → the audio track 0.8; Blinding Lights with the YT
Music hit excluded → Monochrome 1.0; the made-up title → 404. `/api/segments/kJQP7kiw5Fk` → [0-21.8, 249.4-281.5] (290 ms, cached 1 ms).
WebKit 390×844: playback jumped 0 → 21.9 s with the toast; a seek back to 5 s kept playing the intro; a fake dead
id was replaced and played, the next placeholder resolved ahead, an unmatchable one dropped with its toast; a
current placeholder resolved and played on tap. Lossless keep of FXovf5dsRTw: FLAC 16-bit/44.1 kHz, 229.36 s,
title/artist/album VIDA/ISRC/ReplayGain/synced lyrics, 15.5 s on cable; "Me at the zoo" → no-match → Opus keep.

### To check on the phone

1. Play a music video with an intro (kJQP7kiw5Fk) and watch it jump; lock the screen mid-song: skips still happen
   only while the page gets `timeupdate`.
2. `PATCH /api/settings {"keep_lossless": true}` and keep a popular song; Downloads shows LOSSLESS, Navidrome gets a
   .flac (bigger files on the phone's storage).

## Round 14 (2026-10-22): more lyrics providers, timing offset, manual search (production plan P1.3)

- **Chain** (`lyrics.py` `_chain`; sources in `lyrics_providers.py`), display only: order = setting `lyrics_providers`
  (choices `lrclib, simpmusic, youtube, betterlyrics`; default **`lrclib, youtube, betterlyrics`**: SimpMusic's API
  answers 403 "only available to SimpMusic clients" to anyone else, checked live; it stays selectable). Each source
  runs on its own daemon thread: 8 s each, 25 s per lookup; the first line-synced answer ends it, except the
  word-timed source (BetterLyrics, started at once next to the others) gets ≤ 4 s more and wins with `words`.
  Synced > plain > instrumental; plain only as the fallback. 401/403/429 → that source paused 10 min; network /
  5xx / bad JSON / no answer in 8 s → 60 s (LRCLIB retries a 502/503/504 once after 0.4 s first: it 503'd about one
  request in three on 2026-10-22). One lookup per track id (stripes). Log line per lookup: `lyrics id=… provider=…
  complete=0|1 ms=… asked=lrclib:synced,betterlyrics:words` (also `:none :plain :error :403 :timeout :paused :cut`).
- **SimpMusic** `GET api-lyrics.simpmusic.org/v1/{videoId}` → `data[]` (or a bare list / one object):
  `richSyncLyrics` (enhanced LRC → words) > `syncedLyrics` > `plainLyric`, then votes. **YouTube Music**:
  `ytmusic.lyrics(id)` = `get_watch_playlist` → `lyrics` browseId → `get_lyrics(timestamps=True)` (line-timed via the
  mobile client when YouTube has it, else plain); "No content returned" / unreadable page = none (no pause).
  **BetterLyrics** `GET lyrics-api.boidu.dev/getLyrics?s=&a=&al=&d=` → `{ttml}`; 401 "Uncached … API key" = none (no
  pause); TTML parsed with `xml.etree` after refusing any `<!DOCTYPE`/`<!ENTITY` (no DTD → no entity expansion, no
  external entities; an undeclared `&x;` is a parse error); `<span begin end>` = words (`ttm:role="x-bg"` containers
  included), `<body dur>` more than 10 s off the track's length = another recording → none.
- **Checks on every answer** (`lyrics_providers.result`): `[mm:ss:xx]` → `[mm:ss.xx]`; credit lines ("Synced by",
  "Lyrics by", "Written by", "Composed by", "Lyrics & music by", "Lyrics from", "Source:", 作词/作曲) dropped among
  the first/last 3 lines only; synced text whose lyric lines span < 10 s or whose first line starts after
  duration + 10 s is kept as plain; < 3 lines = junk (as before). LRCLIB keeps /get (±2 s) then /search (±5 s,
  closest first). Words are dropped unless there's one entry per LRC line as the client's parseLRC reads them.
- **Embedding unchanged**: `for_track` (keeps) returns LRCLIB's part only (the entry keeps `lrclib` when another source
  won; a pinned LRCLIB record counts); `for_display` (the view) returns the winner. History `lyrics=` records what was
  embedded.
- **Cache** (`lyrics.json`, entry `v: 2`): hits forever; a hit from an incomplete lookup (a source before it failed /
  was paused, or the word wait was cut) gets `retry` and is re-asked after a day (served meanwhile, kept if the
  refresh finds nothing); misses need a complete lookup, last a day, and only count for the same source list
  (`sig`). Reef 1 entries (no `v`): LRCLIB synced hits stand, plain hits and misses are asked again. Words are stored
  packed `[[t ms, d ms|null, text]]` per line (lyrics.json is rewritten whole per new entry). Pinned entries are
  evicted last.
- **Offset**: `offset_ms` (+ = lyrics later) stored in the entry, kept across re-lookups, reset by a pin. Works for
  library tracks too (an offset-only entry next to Navidrome's tags). **Pin**: provider `lrclib-pinned`, never
  replaced by lookups, beats Navidrome's tags for `nd:` tracks, embedded by a later keep (already-kept files keep
  their old tag).
- **Client** (`lyrics-core` section = `LY` in app.js: parseLRC moved there, `attachWords`, `indexAt`, `wordAt`,
  `lyricTime`, `seekTime`, `nudge`, `fmtOffset`): word-timed lines render one `<span class="w">` per word, lit up to
  the current word on `timeupdate` (DOM touched only when the word changes); offset applied to highlighting and
  tap-to-seek. A ⋯ in the lyrics panel's top-right corner opens a small popover: Timing −0.5 / value (tap = reset)
  / +0.5 (saved 600 ms after the last tap), "Wrong lyrics? Search", "From <source> · word timing". The **Find
  lyrics** sheet searches LRCLIB (first by the track's title + artist, ranked by `matching.score`), tap a result =
  pin; "Use automatic lyrics" unpins. `GET /api/lyrics/<id>` is now `Cache-Control: private, no-cache`.

### API shapes

- `GET /api/lyrics/<id>?title=&artist=&album=&duration=` → `{synced, plain, instrumental, words?: [[{t, d?, text}]]
  (seconds; one list per LRC line), provider: lrclib|lrclib-pinned|simpmusic|youtube|betterlyrics|navidrome,
  offset_ms}`; 404 `{error, offset_ms}`.
- `PUT /api/lyrics/<id>/offset {ms: -10000..10000}` → `{offset_ms}`; 400 bad value, 404 bad id.
- `GET /api/lyrics/search?q=[&title=&artist=&duration=]` → `{q, results: [{id, track, artist, album, duration,
  synced, plain, instrumental, score?}]}` (≤ 20, junk dropped; `q` defaults to `matching.search_text` of the hints;
  `score` + ordering when hints are given); 400 nothing to search, 502 LRCLIB unavailable.
- `PUT /api/lyrics/<id>/pin {lrclib_id}` → the GET shape (provider `lrclib-pinned`, offset 0); 400 bad id, 404 no such
  record / no real lyrics in it, 502 LRCLIB unavailable. `DELETE /api/lyrics/<id>/pin` → 204 / 404.

### Measured on the Mac (live, port 5309, scratch data)

Never Gonna Give You Up → betterlyrics (LRCLIB synced + 58 word-timed lines) 0.63 s (0.39 s again after
the unpin); Despacito (FXovf5dsRTw) → betterlyrics, 81 lines, 0.21 s; Sezen Aksu "Gidiyorum", Ivete Sangalo, Dalida,
Cesária Évora, Fela Kuti, Bonga → youtube line-synced 0.8-2.5 s (LRCLIB was 503ing / paused then, so marked
`retry`); Tinariwen → youtube plain; Ana Gabriel "Simplemente Amigos" → betterlyrics words; Mariza, Oumou Sangaré
→ lrclib (BetterLyrics: none); "Me at the zoo" → 404. SimpMusic on: `simpmusic:403` → paused 600 s. Offset / pin /
unpin / search round trips as in the API shapes. WebKit 390×844: word highlight progressing in the active line,
nudges saved (+1.5 s on the server), the sheet's 18 LRCLIB results, a pin replaces the view (plain → timing
disabled), unpin brings the word-timed lyrics back, offset survives a reload, tap-to-seek lands at stamp + offset.

### To check on the phone

1. Open lyrics on a popular song: BetterLyrics word highlighting on iPhone Safari (timeupdate ~4/s: words light in
   small groups); nudge timing and reopen the app.
2. "Wrong lyrics?" → pick another result → it stays after a restart; keep the song → the file has the pinned text.

## Round 15 (2026-10-22): anonymous personalisation, charts, artist aliases (production plan P1.2, P2)

New settings `yt_personalize` (default **off**, `REEF_YT_PERSONALIZE`), `yt_visitor_id` (masked), `charts_country`
(default `ZZ` = global, `REEF_CHARTS_COUNTRY`); new file `aliases.json` (git-ignored, `REEF_ALIASES_FILE`, next to
history.json).

- **Personalisation** (`personal.py`): with `yt_personalize` and `ytmusic` both on, Reef keeps one visitorData for
  good (`yt_visitor_id`: made on first use from `music.youtube.com/sw.js_data`, the homepage's ytcfg as fallback;
  paste one to carry a taste profile over, blank = a new one next time). `ytmusic._client()` rebuilds the shared
  YTMusic with that id in `X-Goog-Visitor-Id` + `context.client.visitorData` (pre-filling ytmusicapi's
  `base_headers` cache, so no homepage fetch) whenever the setting or the id changes; off = ytmusicapi's own
  throwaway id again. The player (app.js `personalPing`, only when `state.settings.yt_personalize` and a YouTube id)
  posts `/api/personal/play` when a play starts (the listen log's new `lis`) and `/api/personal/progress` every 10 s
  of actual playing. One daemon thread (`reef-personal`, queue of 32) does the play sequence: InnerTube `player`
  (WEB_REMIX, `signatureTimestamp` read from base.js once a day: without it YouTube answers UNPLAYABLE with no
  `playbackTracking`) → GET `videostatsPlaybackUrl.baseUrl&ver=2&c=WEB_REMIX&cpn=<16>`; heartbeats GET
  `music.youtube.com/api/stats/watchtime?ns=yt&ver=2&c=WEB_REMIX&cmt=&docid=&cpn=` (same cpn; server throttle 10 s per
  play, only for the id /play started). All requests carry the visitor header plus `VISITOR_INFO1_LIVE` (decoded
  from the visitorData) and `SOCS=CAI` cookies. Failures: debug log, dropped; any 4xx pauses everything 10 min.
  Off: the routes answer `{queued: false, reason: "off"}` and nothing is sent (work queued before the switch is
  dropped). One info line per reported play: `personal play id=… player=200 videostats=204 ms=…`.
- **YouTube Music's own Home**: with personalisation on, a Home build also stores `get_home(6)` shelves (ids
  `ytm:<slug>`, `source: "ytmusic"`; songs-only rows = `tracks`, mixed rows = `cards`), served after Reef's shelves
  and before Charts/Moods, minus anything an earlier shelf shows. `?ytm=0` leaves them out; turning the setting on or
  off makes a rebuild due (and hides them at once).
- **Charts** (`charts.py`, `ytmusic.charts` / `chart_playlist`): the anonymous FEmusic_charts page only has video
  playlists (Daily / Top 100 Music Videos, Trending 20, US genres, IN languages) + top artists (+ top albums and
  podcasts for some countries; ytmusicapi 1.12.3's `get_charts` takes the US albums for the artists, so Reef reads
  the page per carousel itself, falling back to `get_charts`). Sections: **Top 100 Songs** (`tracks`; Global's
  playlist id is known, other countries' are found by an exact-title search among YouTube Charts' `PL4fGSI1pDJn…`
  playlists: US and IN found, DE/BR/JP/GB/FR/MX not, as of 2026-10-22; the lookup is cached 7 days, misses too),
  **Top 100 Music Videos** (`videos`), **Trending 20** (`trending`, not for ZZ), **Top artists** (`artists`).
  Tracks carry `rank` (assigned before blocked artists are dropped). Cache per country 6 h in memory (30 min when a
  section failed; a failed refetch serves the old copy as `stale` and waits 10 min). Home's **Charts** shelf (top
  10 songs, else top 10 videos, of `charts_country`) reads that cache only; the Home refresher thread warms it.
  Chart rows are mostly official videos: their art is 16:9 `i.ytimg.com` (YT Music gives no square art for them).
- **Aliases** (`aliases.py`): `{id: UC… | null, match: name | null, name}`; id beats name; names compare like the
  blocklist's (`BeyonceVEVO` = `Beyonce` = `Beyonce - Topic`). Applied at the response boundary: `_mark_kept`
  (every track list: search, radio, link, album, artist, playlist, likes, playlists, charts, Home), album
  `artist(s)`, artist page `name` + related, Home seeds / top artists / cards. Caches and stores keep the source's
  names (the resolver copies `artists` lists). Keeps: `downloader._tags` passes the alias to `music_tags` (artist +
  album artist; the uploading channel's id names a lone artist) and over album-page hints; the generic path tags the
  aliased artist / album artist. Lyrics lookups, history and matching keep the source's names.

### API shapes

- `POST /api/personal/play {id}` → 202 `{queued: true}` / 200 `{queued: false, reason: off|not_youtube|paused}`; 400
  bad body. `POST /api/personal/progress {id, t}` → same, reasons + `no_play|throttled|bad_time`. Both accept a
  beacon body (any Content-Type). `GET /api/personal/status` → `{active, visitor (masked), paused_until, sent,
  failed, dropped, current: {id, started_at}, recent: [{kind: player|videostats|watchtime, id, status, at, error?}]}`
  (diagnostics carries it without `recent`).
- Settings: `values.yt_visitor_id` = `{set, masked: "CgtF…TVFr"}`; schema row `type: "text", masked: true,
  default: null`. PATCH a pasted id (`Cg…`, ≤ 1002 chars) or `""` to reset. `charts_country` is upper-cased.
- `GET /api/charts?country=ZZ|US|…` (default: the setting) → `{country, name, countries: [codes], generated_at,
  stale?, sections: [{title, kind: tracks|videos|trending, playlist_id, items: [track + {rank, kept, liked,
  nd_id?}]} | {title: "Top artists", kind: "artists", items: [{id, name, rank, trend: up|down|neutral|null,
  subscribers, thumb}]}]}`; 400 bad code / not in YouTube's list (with `countries`), 502 YT Music off or down.
- `GET /api/home` shelves may now include `{id: "charts", title: "Charts", kind: "tracks", country, name, section,
  items}` and `{id: "ytm:<slug>", title, kind: tracks|cards, source: "ytmusic", items}`; a card is `{type:
  track|album|playlist|artist, id, title, subtitle, thumb, track? (for type track, marked kept/liked)}`.
- `GET /api/aliases` → `[{id, match, name, at}]`; `PUT /api/aliases {name, id?, match?}` → 201/200 `{entry,
  created}` (same id, or same name when the entry has no id, = rename); 400 bad body. `DELETE
  /api/aliases?id=|match=` → 204 / 404 / 400.

### Measured on the Mac (live, port 5310, scratch data)

`/api/charts?country=ZZ` cold 1.38 s (tracks 99, videos 100, artists 40), warm 4 ms; `US` 2.1-2.6 s (tracks 100
once the community-playlist search found it, videos 99, trending 20, artists 40: Drake #1, not the albums
carousel). Personalisation ON with a fresh id: id made in ~0.9 s, `player=200 videostats=204 ms=359`, two heartbeats
`watchtime 204`, 204; the next Home build (0.95 s) added 8 YouTube Music shelves (Quick picks 20 songs, Throwbacks,
Pump it up, Low key vibes, Trending community playlists, All-time essentials, Today's biggest hits, Trending songs for
you 14). OFF (before and after): play + 2 heartbeats answered `reason: off`, status counters unchanged, no new
`personal` log lines, the rebuild dropped the YT shelves. Aliases: id alias `Beyoncé → Бейонсе` showed in search,
radio and the artist page; name alias `DaftPunkVEVO → Daft Punk (FR)` matched YT Music's "Daft Punk"; delete →
original names.

### Privacy notes

- Off by default; nothing goes to YouTube's stats endpoints unless the user turns it on. With it on, every play of a
  YouTube track (not Monochrome / library-only ones) and its listening progress is tied to one long-lived anonymous
  YouTube visitor id, and all YT Music requests (search, radio, browse) carry that id too, so YouTube can build a
  profile of this Reef install (and of the phone's IP). No Google account is involved.
- The id lives in `settings.json` (masked in the API, diagnostics and logs); it isn't in backups (backup.py's settings
  whitelist), so a restore on another install starts a new profile unless the id is pasted. Clearing it starts
  over; YouTube keeps whatever it already learnt about the old one.
- `/api/personal/status` lists the last 20 reported video ids (local only, like the listen log).

### To check on the phone

1. Settings → turn personalisation on (no UI toggle yet: `PATCH /api/settings {"yt_personalize": true}`), play a
   few songs for a day, then compare `/api/home` `ytm:quick-picks` with a fresh install's.
2. Watch `logs/reef.log` for `personal play … videostats=204` and that nothing appears with the setting off.
3. `/api/charts?country=<yours>`: whether a Top 100 Songs section is found for the phone's country.

## Round 16 (2026-10-22): playlist import, resumable Monochrome keeps, iTunes suggestions, backup coverage (production plan P2)

New setting `itunes_suggestions` (default on, fallback only). No new files on disk (import state is in
`playlists.json`; a run's progress is in memory). Deliberately **not** done from the P2 table: Spotify embed import
(against Spotify's terms), JioSaavn (legally questionable), the fast InnerTube path (breaks with PO tokens), loudness
at play time (a Web Audio GainNode can break iOS background playback).

- **Import** (`importer.py`, `POST /api/import`). Columns found by normalised name (lowercase, no punctuation):
  title (`Track Name`, `title`, `song`, `track`, `name`, …), artist (`Artist Name(s)`, `artist(s)`, `creator`, Takeout's
  `Artist Name 1..n`; older Exportify's `A;B` → `A, B`), album, duration (`… (ms)` columns in ms, `3:45`, seconds,
  numbers ≥ 20000 read as ms), ISRC, a playlist-name column (TuneMyMusic), a video id. Delimiter sniffed (`, ; tab`),
  BOM / UTF-16 / Latin-1 tolerated. **Takeout by row shape**: a `Video ID` column (header found within the first 20
  rows, so the older playlist CSVs with a metadata preamble work), or header-less rows that are only an 11-char id +
  timestamp; name from the file name minus `-videos.csv` / `.csv`. Video-id rows become real tracks (an untitled one
  gets its title from `ytmusic.song`, else shows its id); every other row a placeholder `{id: "pq:<12 hex>", q: true,
  title, artist, channel, duration, album?, isrc?, key: matching.key_for(title, artist), miss?, miss_at?}`
  (`playlists.clean_entry`; `POST /api/playlists` and `/tracks` accept them too, cover_id skips them, `.m3u` comments
  them out). The playlist stores `import: {kind, rows, skipped, total (rows left to match), at}`.
- **Matching**: `app._find_song(loose=True)` = matching.best (same thresholds, title floor) then
  `importer.loose_best`: accent-folded **word-subset** titles (one side's normalised words ⊆ the other's), artist
  Dice ≥ 0.5, same version words, within 10 s when both lengths are known (else ≤ 3 words apart), raw ≥ 0.35 —
  "Kiss from a Rose (From Batman Forever)" for "Kiss from a Rose". `/api/match` uses it too (not `/api/replace`).
- **Background pass** (`POST /api/import/<id>/resolve`, also started by the import unless `resolve` is false): one
  thread per playlist, rows in order, matches.json first (a manual fix or a row the player matched costs no request),
  every YouTube Music request through `importer.throttle` (≥ 300 ms apart, shared by all runs). Found → the placeholder
  is swapped **by id** (`playlists.replace_entries`, so moves/removals meanwhile are safe) and remembered in
  matches.json; nothing → `miss: true` (listed in status). A 429 pauses `max(60 s, Retry-After)` (≤ 15 min) and the run
  carries on; 3 in a row stop it (`error`), as do 5 other failures in a row (a failed row is retried by the next run).
  `retry_missing` clears the misses first. Deleting the playlist cancels the run. GET of the playlist and of the
  status swap placeholders that already have a cached match (the player's lazy `/api/match` lookups) — no network.
- **UI**: Kept now lists Reef playlists (`/api/playlists`, "N to match · M not found") with an **Import** chip; Server sheet →
  **Import playlist** sub-sheet (file + optional name → "Imported …: N songs, M to match" + Open playlist). Page
  `#local/<id>` (Play / Shuffle / Keep all, rows; placeholders dimmed with the app icon, "Not matched yet" / "Not
  found", no + and only Play next / Add to queue in ⋯), a status line polled every 2 s while matching ("Matching
  songs… 7 of 10", "Match N more", "Try missing again") that reloads the rows as they match. Placeholders play
  through match-core as before; `artUrl` gives `pq:` ids the app icon (the mini player asked `/api/art/pq:…` → 404).
- **Resumable generic keeps** (`downloader._fetch`): downloads to `<raw>.part`; a retry (2/4/8 s backoff, three
  retries, as before — but an attempt that got ≥ 256 KiB costs no retry and resets the backoff; 50 attempts at most)
  sends `Range: bytes=<size>-` and appends on a matching 206 (`Content-Range` start = size, same total),
  starts over on a 200, on a 206 for other bytes or another total, and on a 416 unless the file is already whole; a
  body shorter than its Content-Length counts as a drop. Before each attempt a URL whose `expire` is < 60 s away (or,
  without one, whose streamer cache TTL ran out) is re-resolved (`streamer.resolve` re-extracts by itself then).
  Test hook `REEF_FAKE_DROP=<bytes>`: the first attempt of each generic fetch drops after that many bytes.
- **iTunes suggestions** (`itunes.py`): `/api/suggest` asks `itunes.apple.com/search?term=&media=music&entity=song,
  musicArtist&limit=10` when YouTube Music is off or its call raised (an empty YT Music answer stays empty). Songs →
  "Artist - Song", artists → name (music videos and collections skipped), deduped case/space-insensitively, ≤ 8;
  3 s timeout; cached 10 min (ytmusic's `_TTLCache`); a failure answers [] and pauses iTunes 60 s.
- **Backup**: new sections `aliases` (merge by channel id / name key, newer `at` wins) and `matches` (**manual
  fixes only**; restore never touches automatic entries except one under a key a restored fix takes; merge: newer
  `at` wins). Lyrics now carry v2 entries whole (provider, packed words, `lrclib`, `retry`, pins with `lrclib_id`),
  timing offsets, offset-only entries (library tracks) and misses that carry an offset (as offset-only); plain misses
  are still dropped. Lyrics merge: a pin beats a lookup, else newer `at`; an offset-only entry brings only its offset
  (newer wins); an offset the winner lacks is carried over unless the winner is a pin. Playlists keep placeholders.
  `SETTINGS_KEYS` += `yt_personalize`, `charts_country`, `itunes_suggestions` (never `yt_visitor_id`, tokens, URLs).
  `dislikes` was already a section. Same `schemaVersion` 1: an older Reef ignores the new sections.

### API shapes

- `POST /api/import` multipart `file` (+ `name`, `resolve=0|1`) or JSON `{text, filename?, name?, resolve?}`: the CSV
  ≤ 5 MB either way (413 above); the request body may be up to 20 MB (`importer.REQUEST_MAX`: JSON escaping of a
  quoted, non-ASCII CSV grows it ~1.3x+; a 4.5 MB Exportify-style CSV is a 5.8 MB JSON body). Per-request
  `max_content_length` (here and in `/api/restore`) needs **Flask ≥ 3.1**, now the floor in requirements.txt (the
  phone's `update.sh` pip step upgrades it); the rest of the app keeps 4 MB → 201 `{playlist: summary, kind: csv|takeout, rows, skipped, to_match, status}`; 400 `{error}` (no
  usable columns / rows). Playlist summaries gain `unmatched` (placeholders still to look up) and `not_found` (misses), each when > 0; playlists may carry `import`.
- `POST /api/import/<id>/resolve {retry_missing?}` → 202 status; 404; 502 YouTube Music off.
- `GET /api/import/<id>/status` → `{total, resolved, pending, missing: [{id, title, artist, album?}], running,
  rate_limited, retry_at, error, failed, tracks, started_at, finished_at}`.
- Backup `sections` += `aliases: [{id, match, name, at}]`, `matches: {key: {id, score, manual: true, at, track}}`.

### Measured on the Mac (live, port 5311, scratch data)

Exportify-style CSV of 10 songs (incl. "Bohemian Rhapsody - Remastered 2011", "Corazón Partío") → 10/10 matched in
~7 s; a row "Qwzzlxtr Florbnag Vemp" → listed in `missing`. Takeout `Road Mix-videos.csv` (preamble + Video ID rows)
→ "Road Mix", 3 tracks titled via YT Music. `/api/suggest?q=daft punk` with `ytmusic` off → 8 iTunes strings (0.59 s
cold, 21 ms cached). Live Monochrome FLAC (40.6 MB) through `_fetch` with `REEF_FAKE_DROP=1500000`: the injected drop
plus a real one (tracks.monochrome.st cut a plain GET at 11 MB) resumed, sha256 identical to a ranged reference
copy; a server keep with the hook (drop at 2 MB) completed. Backup → change alias / undislike / offset / manual
match → restore (replace) brought all four back. WebKit 390×844: Kept playlists, the import sheet, an imported page,
"Match 3 more" → polling to "2 of 3 matched · 1 not found"; Play on an unmatched playlist resolved the placeholders
through /api/match (playback itself hit YouTube's bot wall on this Mac's IP at the time — unrelated to this round).

### To check on the phone

1. Server → Import playlist with an Exportify CSV from iPhone Files; open it from Kept while it matches.
2. Keep a Monochrome track over cellular; `logs/reef.log` shows `keep fetch have=… retrying` lines when it resumes.
3. Turn YouTube Music off in Settings and type in search: suggestions still come (from iTunes).

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
