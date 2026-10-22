# Reef production plan

What Reef still needs to be production ready, compiled 2026-10-21 from a read of seven open-source music apps plus an
audit of Reef itself (at `697592d`, after rounds 1-5). It complements `docs/INTEGRATION_LIST.md`: items marked
"→ IL x.y" refine that list's entry with concrete numbers; everything else is new.

| Repo | What it is | Most useful for Reef |
|---|---|---|
| [Metrolist](https://github.com/MetrolistGroup/Metrolist) | Kotlin YT Music client (InnerTune family) | Playback error recovery, stream URL cache, radio fallback chain, InnerTube request shapes |
| [ArchiveTune](https://github.com/rukamori/ArchiveTune) | Metrolist relative with its own InnerTube core + lyrics submodule | Client fallback order, 7-provider lyrics chain, play-time SponsorBlock, ListenBrainz |
| [Flow](https://github.com/Flow-Tube/Flow) | Kotlin privacy-first YouTube/YT Music client | 403 classification + client gating, playability verdicts, diagnostics, radio/quick-pick rules |
| [Meld](https://github.com/FrancescoGrazioso/Meld) | Metrolist fork: Spotify metadata, YouTube playback | Host cooldowns, cross-source matching numbers, lazy queue resolve, structured logging |
| [Gyawun](https://github.com/sheikhhaziq/gyawun_music) | Flutter YT Music client | Mid-stream resume, anonymous visitorData personalisation, home feed layout |
| [Musify](https://github.com/gokadzev/Musify) | Flutter YouTube music app | Player error policy, audio format filter, log viewer, seed-based recommendations, live client tests |
| [BloomeeTunes](https://github.com/HemantKArya/BloomeeTunes) | Flutter + Rust plugin player (sources in kojima-ui/bloom-factory) | Error categorisation, Last.fm stations/scrobbling, charts, backup format |

Already done in Reef, not repeated here: proper tags, square covers, keep retries, duplicate guard, SponsorBlock on keep,
LRCLIB lyrics + lyrics view, ReplayGain on keeps, Media Session, shuffle/repeat, play next / up next, sleep timer,
session restore, prefetch, iOS fragmented-m4a duration fix.

Priority: **P0** = needed before calling it production ready, **P1** = strong value, **P2** = nice. Effort: S < 1 day,
M a few days, L a week+.

---

## P0. Reliability and operations

The single loudest finding: all seven apps treat playback failure as normal and recover from it. Reef's player only
shows "Couldn't play this one" (`static/app.js`, the `audio` `error` listener), and the proxy refreshes a dead URL
once with the same client.

### P0.1 Player recovery (S)
Sources: Metrolist `MusicService.kt:1505-1600,2680,5055`; Musify `audio_service.dart:125-130,846-876,2201-2229`;
BloomeeTunes `lib/services/player/player_error_handler.dart`; Flow `player/MusicPlaybackRecoveryPlanner.kt`.

- On `error` and on `stalled`/`waiting` lasting > 8 s: remember `currentTime`, reload
  `/api/stream/<id>?fresh=1` (server evicts the cache entry and re-resolves), seek back, play.
- Up to 3 attempts per track, backoff 1 s / 2 s / 4 s (cap 10 s).
- Retry the item that failed, not whatever is current (a prefetch can fail while the previous track plays).
- A `playToken` counter increments on every track change; any async step whose token is stale is dropped.
- Still failing: toast and skip to next, but only if that track is still current.
- Failure cap: stop (pause + "Check your connection") after 3 unplayable tracks in a row, or when failures equal
  the queue length (so repeat-all can't spin). A successful `playing` event resets the counter.
- `window` `online` event: resume, but only if Reef paused because of the outage.
- Classify for the toast: expired (403/410), network, source (removed/private), using the JSON `block` from P0.4.

### P0.2 Stream URL lifetime and client gating (M)
Sources: Flow `player/error/StreamDenialClassifier.kt`, `player/stream/ClientGateTracker.kt`,
`player/datasource/StreamSwapTable.kt`; ArchiveTune `YTPlayerUtils.kt:42-139,515-560,1529-1536`;
Metrolist `StreamUrlCache.kt:37-121`; Meld `QobuzAudioProvider.kt:1360`.

In `streamer.py` / `sources/youtube.py`:
- **TTL from the URL**, not a flat `_TTL = 3600`: read `expire=` (googlevideo), `Expires=`/`etsp=` for others; use
  `expire - 60 s`, default 300 s. Treat a cache hit as usable only if remaining life ≥ track duration + 60 s.
- **Classify an upstream 403** using the cached URL's own params:
  - `expire` within 30 s → plain expiry, re-resolve with the same clients (today's behaviour).
  - `expire` valid, no `pot` → the client is being denied: gate client `c=` for 30 min, re-resolve with the next one.
  - valid with `pot` → token rejected: gate on the second strike.
  - Map `c=` to yt-dlp names: ANDROID_VR→`android_vr`, TVHTML5→`tv`, MWEB→`mweb`, WEB→`web`, IOS→`ios`, VISIONOS→`visionos`.
- Gate per `(video_id, client)` for 10 min on 403/404/410/416, globally per client for 30 min on repeated denials.
  Don't reuse a cached URL whose client is gated. Expose gated clients in `/api/health`.
- A generation counter per id so a slow resolve can't overwrite a newer invalidation.
- **Mid-song URL swap**: two URLs with the same `itag` + `lmt` + `clen` serve byte-identical files, so a refreshed URL
  can keep serving Safari's Range requests transparently; otherwise return an error and let P0.1 recover.
- **Reject previews**: if the format's `approxDurationMs ≤ min(90 s, 0.9 × duration)` and the track is > 90 s, try the
  next format/client.
- **Format filter**: `bestaudio[ext=m4a][acodec^=mp4a]/bestaudio[ext=m4a]/bestaudio` so Dolby `ec-3`/`ac-3` or 5.1
  streams never win (Musify excludes them on purpose).
- Test hook: `REEF_FAKE_WALL=1` returns 403 for Range starts past `clen × 60 / duration` (Flow
  `FakeStreamWallDataSource.kt`), to exercise recovery without waiting for YouTube.

Client notes: on the phone (2026-10-21) the yt-dlp default resolved audio-only m4a in 1.2 s; `android_vr` returned
format 18 (muxed mp4); `ios`, `web_safari`, `tv`, `mweb` failed. Two repos report YouTube walling `android_vr` since
2026-08 (cut-off ~60 s into playback) and yt-dlp's tokenless default moving to `visionos`. Keep `REEF_YT_CLIENT`
empty and benchmark `visionos` next time. ArchiveTune's fallback order: VISIONOS, ANDROID_VR, WEB_REMIX, WEB, MWEB,
WEB_CREATOR.

### P0.3 Mid-stream resume in the proxy (S) → IL 5.2
Source: Gyawun `lib/services/stream_client.dart:29-97`.
In `streamer.proxy`'s `generate()`, count bytes sent; on `ChunkedEncodingError` / `ReadTimeout` / connection reset,
reopen upstream with `Range: bytes=<start+sent>-<end>` and keep yielding (5 retries, 500 ms apart). Safari never sees
the drop. Same for the Monochrome FLAC path.

### P0.4 Typed failures and "Reset YouTube session" (S-M) → IL 5.5
Sources: Flow `player/stream/PlayabilityVerdict.kt`, `player/stream/ResetYouTubeSession.kt`.
- Stream/download errors return JSON `{block: BOT_WALL | NETWORK | LOGIN_REQUIRED | REGION | RATE_LIMITED |
  MEMBERS_OR_PAID | GONE | UNKNOWN, message}`. Patterns: `not a bot|confirm you.?re`, `in your (country|region)`,
  `try again later|429`, `members-only|join this channel|requires payment|premium members`, `private|removed|unavailable`.
- `GONE` only when every client answered unplayable; a timeout is never `GONE`.
- All clients bot-walled → reset once and retry; still walled → "Try mobile data or turn off VPN".
- Reset = clear yt-dlp's cache dir, rebuild the shared `YoutubeDL`, clear gates and the resolve cache. A button in the
  status sheet, and `POST /api/yt/reset`.

### P0.5 Failover cooldowns (S)
Source: Meld `qobuz/QobuzAudioProvider.kt:116-196`.
`sources/failover.py`: a `host → until` dict. Cool a host down 3 min on DNS failure, refused connection, timeout, 5xx,
or an HTML body where JSON was expected (starts with `<`); 5 min on 403/captcha. Skip cooling hosts; if all are
cooling, try the one that expires first.

### P0.6 Run it like a service (S) — from auditing Reef
- **WSGI server**: `app.run()` is Werkzeug's development server. Serve with `waitress` (pure Python, works in Termux):
  `waitress.serve(app, host=..., port=..., threads=8, channel_timeout=120)`; keep streaming responses working
  (`send_bytes` buffering off for `/api/stream`).
- **Supervisor**: `termux/reef-boot.sh` starts Reef once; a crash leaves it down until reboot. Wrap it in a restart
  loop with backoff (`while :; do python app.py; sleep $((n<60?n*=2:60)); done`) or use `termux-services` (runit `sv`).
  Same for Navidrome.
- **Exposure**: Reef binds `0.0.0.0`, so it is open on home Wi-Fi (192.168.x) as well as the tailnet, with no
  password. Default `REEF_HOST` to the Tailscale address when present, or set `REEF_PASSWORD`. The Navidrome admin
  password is weak; change it (Reef only needs it for `startScan`).
- **Logs**: Python `logging` with `RotatingFileHandler` (`logs/reef.log`, 1 MB × 5) instead of an ever-growing
  `reef.log` from `nohup`. One `op k=v` line per event with a correlation id (`res-17`); redact signed-URL params
  (`sig`, `signature`, `pot`, cookies) as `len=N,sha1=xxxx` (Meld `utils/Fix403.kt`).
- **Diagnostics**: a `deque(maxlen=300)` of recent log lines + yt-dlp version, gated clients, cache stats at
  `GET /api/diagnostics`, and a "Copy diagnostics" button in the status sheet (Flow `PlayerDiagnostics.kt`,
  Musify `logger_service.dart`).

### P0.7 CI (S)
Sources: Musify `test/fetch_audio_stream_live_test.dart`, BloomeeTunes `.github/workflows/checkout.yml`,
Metrolist `ParserRegressionTest.kt`.
- GitHub Action on push/PR: `pip install -r requirements-dev.txt && python -m pytest -q` + `ruff check`; skip docs-only
  changes.
- A `-m live` pytest marker parametrised over player clients (resolve one id, HEAD it with that client's headers),
  skipped when `GITHUB_ACTIONS` is set because YouTube walls CI IPs. Run it on the phone.
- Once P1.1 exists: saved InnerTube JSON fixtures + parser regression tests.

---

## P1. Biggest product gains

### P1.1 YouTube Music InnerTube module (M) → IL 3.1-3.5
Sources: ArchiveTune core `InnerTube.kt:175-276`, `YouTube.kt:2485-2497,2736`, `youtube_clients.json`; Metrolist
`YouTube.kt:137-165,1533-1700,1939,2883-3063`; Gyawun's `yt_music` `browsing.dart:10-116,280-455`;
Musify `music_client.dart:220-607`. Alternatively wrap `ytmusicapi` (MIT, pure Python) and keep these details for gaps.

- **Request**: `POST https://music.youtube.com/youtubei/v1/<endpoint>?prettyPrint=false`, headers
  `X-YouTube-Client-Name: 67`, `X-YouTube-Client-Version: 1.2026MMDD.xx.00` (WEB_REMIX), `X-Origin`/`Referer:
  https://music.youtube.com`, `X-Goog-Visitor-Id`, cookie `CONSENT=YES+1`; body `context.client {clientName,
  clientVersion, gl, hl, visitorData}`. Keep client versions in a JSON file so they update without code changes.
- **visitorData**: `GET https://music.youtube.com/sw.js_data`, drop the first 5 chars (`)]}'\n`), JSON, the string in
  `[0][2]` matching `^Cg[ts]`. Or scrape `ytcfg.set({...})` from the homepage for `VISITOR_DATA`,
  `INNERTUBE_CLIENT_VERSION`, `HL`, `GL`. Persist it.
- **Search filters** (`search` `params`): songs `EgWKAQIIAWoMEA4QChADEAQQCRAF`; see ArchiveTune `SearchFilter.kt` for
  videos/albums/artists/playlists. Suggestions: `music/get_search_suggestions` (section [1] = instant results).
- **Radio** (biggest UX gain: no more silence after one song): `next` with `{videoId, playlistId: "RDAMVM<id>",
  isAudioOnly: true, enablePersistentPlaylistPanel: true, tunerSettingValue: "AUTOMIX_SETTING_NORMAL"}`,
  `params: "wAEB"` (radio) / `"wAEB8gECKAE%3D"` (shuffle). Fallback chain when it returns ≤ 1 item: bare `videoId`,
  then the related tab (`tabs[2].browseEndpoint`) keeping `MUSIC_VIDEO_TYPE_ATV` songs. Continue via
  `playlistPanelRenderer.continuations` / `&ctoken=X&continuation=X` when ≤ 5 items remain. Dedupe against queue and
  history, 10 s timeout, and drop the result if the playing track changed meanwhile. Off when repeat-all is on.
  Shape rules (Flow `MusicRadioPlanner.kt`): pool ≤ 100, ≤ 2 tracks per artist, never the same artist back to back,
  refill from anchors (seed + first page's top 8) rather than the tail so the station doesn't drift.
- **Whole album/playlist tracklist**: the same `next` call with a bare `playlistId` (strip `VL`; albums via their
  `OLAK5uy_` id) → "Keep album" (pair with IL 4.3).
- **Home**: `browse FEmusic_home`; mood chips at `sectionListRenderer.header.chipCloudRenderer.chips[]` (browse again
  with each chip's `params`); more shelves from `continuationContents.sectionListContinuation`. Render a shelf as a
  list when `numItemsPerColumn ≥ 4` (Quick picks), else a carousel; a `watchPlaylistEndpoint` in `moreContentButton`
  means "play all". `FEmusic_explore` = new releases + moods in one call. Charts: `FEmusic_charts` with
  `params=ggMGCgQIgAQ%3D` and `formData.selectedValues=[country]`. Replaces the hand-written queries in `tiles.py`.
- **Artist/album pages**: walk JSON recursively for renderer keys instead of fixed paths. Canonical artist id =
  `header.subscriptionButton.subscribeButtonRenderer.channelId` (maps VEVO/label channels to the artist). Discography
  via `moreContentButton` → `MPAD…` browse + `params`, collecting `musicTwoRowItemRenderer` with `MPRE…` ids.

### P1.2 Optional anonymous personalisation (M)
Source: Gyawun `lib/utils/add_history.dart`, `yt_music` `client.dart:50-131`.
Keep one visitorData for good and report plays against it so `FEmusic_home` Quick picks learn your taste without a
Google login: on play start `POST player {videoId, playbackContext.contentPlaybackContext.signatureTimestamp}`, then
GET `playbackTracking.videostatsPlaybackUrl.baseUrl` + `&ver=2&c=WEB_REMIX&cpn=<16 random chars>`; every 10 s
`GET music.youtube.com/api/stats/watchtime?ns=yt&ver=2&c=WEB_REMIX&cmt=<sec>&docid=<id>`. Settings toggle plus
paste/reset visitor id.

### P1.3 More lyrics providers (M) → IL 1.3 / 2.5 (LRCLIB is done)
Sources: Metrolist `LyricsHelper.kt:29-111`, `LyricsUtils.kt:34-70`; ArchiveTune lyrics submodule; Flow
`data/lyrics/LyricsHelper.kt`, `LyricsProviderOrder.kt`; Meld `LyricsHelper.kt:53-130`.
- Chain after LRCLIB, for display only (embed only LRCLIB lyrics into kept files):
  - SimpMusic `GET https://api-lyrics.simpmusic.org/v1/{videoId}` → `data[].syncedLyrics | plainLyric | richSyncLyrics`
    (keyed by YouTube id: no title matching).
  - YouTube's own: `next` → `tabs[1].browseEndpoint` → `browse` → `musicDescriptionShelfRenderer.description.runs`
    (plain). Last resort.
  - Optional word-synced: BetterLyrics `GET https://lyrics-api.boidu.dev/getLyrics?s=&a=&al=&d=` → `{ttml}` (parse with
    `xml.etree`, external entities off; 401 "Uncached" = no lyrics). Unison `https://unison.boidu.dev/lyrics?v={videoId}`.
- Rules: 8 s per provider, 25 s total; after the first line-synced hit wait ≤ 4 s more for word-synced; pause a provider
  10 min after 401/403; one in-flight lookup per id. Prefer synced, keep plain only as fallback.
- Reject results whose timestamps span < 10 s or whose first line starts after duration + 10 s; drop "Synced by /
  Lyrics by / Written by" lines; fix `[mm:ss:xx]` to `[mm:ss.xx]`; LRCLIB match ±2 s first, then ±5 s.
- Extras (BloomeeTunes): per-track timing offset in ms; manual search sheet (`lrclib.net/api/search?q=`) that pins the
  chosen result to the track.
- KuGou, Musixmatch, LyricsPlus mirrors: skip (terms, uptime).

### P1.4 Scrobbling (S-M)
Sources: Flow `data/scrobble/ListenBrainzClient.kt:95-181`, `ScrobbleRules.kt`; ArchiveTune `ListenBrainzManager.kt`;
BloomeeTunes `lastfmapi.dart:140-285`.
- Server-side `scrobbler.py` + `POST /api/listen {id, event: start|progress|end, played_s}`.
- Rule: track > 30 s, counted at min(50 % of duration, 4 min) of actual play (timer pauses with playback).
- **ListenBrainz** (simplest): `POST https://api.listenbrainz.org/1/submit-listens`, `Authorization: Token <t>`,
  `{"listen_type": "playing_now" | "single", "payload": [{"listened_at", "track_metadata": {"artist_name",
  "track_name", "release_name", "additional_info": {"duration_ms", "submission_client": "Reef",
  "music_service": "music.youtube.com"}}}]}`. Disk queue (cap 2000), batches ≤ 100, retry 429/5xx, 401 = signed out.
- **Last.fm** (optional): `api_sig = md5(sorted k+v pairs without format + secret)`; web auth via
  `https://www.last.fm/api/auth/?api_key=K&cb=<reef>/api/lastfm/callback` → `auth.getSession`; `track.updateNowPlaying`,
  `track.scrobble` (`chosenByUser=0` for autoplay), HTTPS only.
- Library (`nd:`) tracks: call Subsonic `scrobble.view?id=&submission=true` instead, so Navidrome's play counts and
  its own scrobbling work. Token/keys live in `reef.env`.

### P1.5 Play kept tracks from Navidrome (S)
Source: Flow `data/download/DownloadUtil.kt:134-161`. When a YouTube/Monochrome id is in `history.json` and has
`nd_id`, play `nd:<nd_id>` instead (already a source). Faster, works when YouTube walls, scrobbles via P1.4.

### P1.6 Matching module (S) → IL 4.2
Sources: Meld `spotify/SpotifyMapper.kt:129-240`, `SpotifyYouTubeMapper.kt:143-349`; Flow
`data/lyrics/LyricsMatching.kt`.
`matching.py`, used by lyrics, lossless upgrade, playlist import, dead-track recovery:
- Normalise: lowercase, strip `(feat…)`, `(ft…)`, `[...]`, `(…remaster…)`, `(…remix…)`, punctuation; first artist only,
  no "- Topic".
- Score: Dice bigram similarity, title 0.45 + artist 0.35 + duration 0.20; duration 1.0 ≤ 2 s, 0.8 ≤ 5 s,
  0.5 ≤ 10 s, 0.2 ≤ 30 s, else 0 (unknown 0.5).
- Version words must agree (live, acoustic, remix, sped up, nightcore, slowed, instrumental, karaoke, cover, demo, 8d,
  music/lyric video): −0.15 each, cap −0.30, ranking only; cutoff on the raw score (accept ≥ 0.35, stop at ≥ 0.95).
- `matches.json` cache keyed by external key → `{id, score, manual}`; a manual "Wrong song? Paste link" fix is never
  overwritten by the automatic matcher.
- Table-driven tests (model: Meld `SpotifyMapperMatchScoreTest.kt`).

### P1.7 Lossless upgrade on Keep (M)
Source: Meld `QobuzAudioProvider.kt:218-370,596-760,1280,1309`.
Opt-in `REEF_KEEP_LOSSLESS=1`: before keeping a YouTube track, search Monochrome by ISRC, then title+artist+album,
narrowing to title; ISRC match +1000, hard reject on remix/live/instrumental/karaoke/sped/slowed mismatch, −45 on
remaster/edit/acoustic/deluxe, reject Δduration > 25 s; minimum score 330; reject if the probed file is < 50 % of the
expected duration (preview). Silent fallback to YouTube. ⚠ Monochrome is a third-party Tidal proxy: legally grey and
only as reliable as it is.

### P1.8 Lazy queue resolve and dead-track recovery (S-M)
Sources: Meld `queues/SpotifyQueue.kt:55-140`; BloomeeTunes `smart_track_replacement_service.dart`,
`cross_plugin_resolver.dart:197-390`.
- Queue entries may be `{q, artist, dur}` (from radio, charts, imports) resolved via `/api/match` only when within 2
  of the current track; unresolvable entries are skipped.
- When a track is `GONE` (P0.4), search `ytmsearch`/Monochrome for the same song (match ≥ 0.45, early stop ≥ 0.93),
  swap it into queue and history, toast it. Setting.

### P1.9 SponsorBlock at play time (S)
Sources: ArchiveTune `SponsorBlockRepository.kt:111-150`, `SponsorBlockPlaybackController.kt:132-147`; Musify
`common_services.dart:564-613`.
`/api/segments/<id>` → `GET https://sponsor.ajay.app/api/skipSegments/<sha256(id)[:4]>?categories=["music_offtopic"]&actionTypes=["skip"]&service=YouTube`
(hash prefix keeps the id private; pick the matching `videoID` from the array). 404 = none, cached; other errors not
cached. Client seeks past each segment once on `timeupdate`. Toggle; YouTube source only.

### P1.10 "For you" shelves from local data (S)
Sources: Musify `common_services.dart:190-303`; Flow `MusicQuickPicks.kt`, `MusicBrainLearn.kt`; BloomeeTunes
`lastfmapi.dart:82-87`.
- Seeds: current track + newest history, one per artist, 5 seeds. For each, related tracks (P1.1); score
  Σ (1 − i/8)(1 − seedIdx/n), drop known ids, keep 15; ≤ 3 per artist; round-robin lanes.
- Listen log (`listens.json`, a play counts at 50 %), artist affinity EMA weight 0.15, recency decay ×0.85/day; a
  dislike = 14-day cooldown, second dislike blocks the artist.
- Optional Last.fm station: `GET https://www.last.fm/player/station/user/<user>/{mix|recommended|library}` returns
  `playlist[]._name`, `artists[]._name`, `playlinks[] {affiliate: "youtube", id: <videoId>}`, playable as-is.
  Undocumented.

### P1.11 Update check and in-app update (S) → IL 5.6
Sources: BloomeeTunes `bloomee_updater_tools.dart:168-190`; Flow `UpdateRepository.kt`; Meld `Updater.kt:41-69`.
`/api/version`: local `git rev-parse HEAD` vs `GET https://api.github.com/repos/dreamingofu/reef/commits/main` → `sha`,
and installed yt-dlp vs `https://pypi.org/pypi/yt-dlp/json` → `info.version`; 6 s timeout, cache 6-12 h. Banner in
the status sheet; "Update" runs `git pull --ff-only`, `pip install -U yt-dlp`, then restarts (via P0.6's supervisor).
"Skip this version".

### P1.12 Backup and restore (S) → IL 5.9
Sources: BloomeeTunes `docs/backup-and-import.md`, `backup_repository.dart`; Meld `BackupRestoreViewModel.kt:127-344`.
`{"format": "reef-backup", "schemaVersion": 1, "createdAt", "sections": {...}}` with `history`, `lyrics`, `matches`,
`listens`, allow-listed settings (never secrets or device paths). Write temp → read back → rename. Restore: refuse
newer schema, validate, stage, snapshot current state first, swap with `os.replace`; merging is idempotent. Daily
automatic snapshot, keep 7.

### P1.13 Source health panel and remote instance list (S-M)
Sources: Meld `QobuzBackendHealthChecker.kt`, `.github/workflows/spotify-hash-check.yml`.
`/api/health/sources`: ping Monochrome, Piped, Invidious, Navidrome, yt-dlp in parallel on demand → ONLINE /
REACHABLE / OFFLINE + latency, shown in the status sheet; instance overrides in settings (blank = default).
Later: a daily Action publishing `instances.json` of working Monochrome/Piped instances, fetched when the local copy
is > 24 h old, bundled list as fallback.

### P1.14 Home from cache (S)
Source: Meld `SpotifyProfileCache.kt:53-80`. Serve `/api/home` from disk instantly and refresh in the background
(6 h when complete, 30 min when it came from a fallback; 1 min after 429, honour `Retry-After`); the client keeps the
last copy in `localStorage` too.

---

## P2. Nice to have

| Item | Notes | Source |
|---|---|---|
| Loudness at play time → IL 2.6 | gain = target − (`perceptualLoudnessDb` ?? `loudnessDb − 7`), clamp −15…+3 dB, target −14 LUFS. Needs a Web Audio `GainNode` (iOS ignores `audio.volume`), which can break background playback: toggle, kept tracks first (ReplayGain tags exist now) | Metrolist `MusicService.kt:5071-5080` |
| Charts shelf | Spotify `GET https://charts-spotify-com-service.spotify.com/public/v0/charts` → `chartEntryViewResponses[0].entries[].trackMetadata` + rank data; Billboard HTML (`o-chart-results-list-row-container`). Resolve a row only when tapped. Unofficial, fragile | bloom-factory `billboard/` |
| CSV / Takeout import → IL 4.4 | Detect columns (`song`/`track`, `artist`); Takeout: detect by row shape, playlist name from filename minus `-videos.csv`; songs-filter search, accent-folded word-subset match, batches, stop on 429, list misses | Musify `import_spotify_playlist_page.dart:120-275`; Flow `YouTubeTakeoutCsvParser.kt` |
| Spotify playlist import | Embed page `open.spotify.com/embed/{playlist,album}/{id}` → `__NEXT_DATA__` `/props/pageProps/state/data/entity` (first 100). Read-only, against Spotify's terms | bloom-factory `spotify-importer` |
| `.m3u` export | `#EXTM3U` / `#EXTINF` into the music folder; Navidrome imports playlists | Metrolist `PlaylistExporter.kt:53-70` |
| Resumable Monochrome keeps | `.part` + `Range: bytes=<size>-`, restart on 200; don't start with a URL expiring in < 60 s | BloomeeTunes `rust/src/api/downloader/transfer.rs:84-140` |
| Artist aliases | `aliases.json` channel id → display name, applied to tags and UI | Metrolist `ArtistNameAliases.kt` |
| Artwork accent colour | sample cover on a canvas (art must be same-origin: `/api/art`) for the Now Playing tint | Gyawun `player_page.dart:90` |
| iTunes suggestions | `https://itunes.apple.com/search?term=&media=music&entity=song,musicArtist&limit=10` (official, no auth; `100x100bb` → `600x600bb`) | bloom-factory `itunes-search-suggestions` |
| Fast YouTube path (experimental) | Direct InnerTube `player` with the ANDROID client before yt-dlp, 3 s budget, behind `REEF_YT_FASTPATH`; breaks when PO tokens are enforced | bloom-factory `ytmusic/src/client.rs:23-52,772-852` |
| JioSaavn source | `www.jiosaavn.com/api.php?__call=search.getResults…`; media URLs are DES-encrypted (key `38346591`), India-only. Legally questionable; if ever, `can_keep=False` | bloom-factory `jiosaavn/` |

---

## Do not adopt

- Meld's Spotify login (TOTP secret from a gist, scraped GraphQL hashes) and its Qobuz backends (piracy proxies).
- ArchiveTune's remote network gate (`NetworkGatekeeper.kt`, `DataServer.txt`) and AI mix calls.
- Any app's own cipher / BotGuard / PO-token stack: yt-dlp (+ `bgutil-ytdlp-pot-provider` if needed) covers it.
- Musixmatch desktop API, LyricsPlus mirrors, KuGou: terms and uptime.
- Shazam recognition, Discord RPC, Android Auto, equalizer/audio effects, widgets: native-only or no fit.

## Licensing

All seven apps are GPL (Metrolist, ArchiveTune, Flow, Meld, Gyawun, Musify GPL-3.0; BloomeeTunes GPL-2.0).
bloom-factory and Gyawun's `yt_music` have no licence (all rights reserved). Reef is MIT: implement from the
behaviour, endpoints and thresholds recorded here, never by porting their code. `ytmusicapi` (MIT) can be a
dependency.

## Suggested order

1. **P0.1-P0.5** in one pass (`static/app.js`, `streamer.py`, `sources/youtube.py`, `sources/failover.py`): the change
   most visible on the iPhone. Test with `REEF_FAKE_WALL` and on the phone.
2. **P0.6-P0.7**: waitress + supervisor + logs/diagnostics + CI.
3. **P1.1** YouTube Music module → radio → home/charts (this is already Phase 4 on the Mac).
4. **P1.4** scrobbling + **P1.5** Navidrome playback for kept tracks.
5. **P1.6** matching → P1.8 dead-track recovery, P1.7 lossless upgrade, P2 imports.
6. **P1.3** extra lyrics providers, P1.9 SponsorBlock at play time, P1.10 For you, P1.11-P1.14.
