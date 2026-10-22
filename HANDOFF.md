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
  `UPDATE_OK` / `UPDATE_FAILED` line. The client polls `/api/update/status` every 2 s, polls `/api/health` while the
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
