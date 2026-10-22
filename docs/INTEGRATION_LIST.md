# Reef integration list

Features worth borrowing from five apps, mapped onto Reef's Flask + yt-dlp + vanilla-JS architecture.
Compiled 2026-10-21 from a full read of:

| Repo | What it is | Relevance to Reef |
|---|---|---|
| [ytdlnis](https://github.com/deniscerri/ytdlnis) | Android yt-dlp GUI (Kotlin), all logic in `YTDLPUtil.kt` + bundled yt-dlp postprocessor plugins | Best source for Keep-pipeline options: tagging rules, crop, lyrics plugin, observe sources |
| [Sealplus](https://github.com/MaheshTechnicals/Sealplus) | Fork of Seal, Android yt-dlp GUI | Retry/back-off profile, queue state machine, cookie paste, format validation |
| [limusic](https://github.com/SimoHypers/limusic) | Rust/Tauri desktop YouTube Music client with own InnerTube stack | Radio/autoplay, synced lyrics aggregator, stream-URL cache rules, loudness, queue semantics |
| [Mornye](https://github.com/mornyeapp/Mornye) | Flutter lossless downloader + player, JS extension sandbox | Metadata enrichment (Deezer/MusicBrainz), ReplayGain, filename templates, sleep timer, session restore |
| [Mornye-Extension](https://github.com/mornyeapp/Mornye-Extension) | Extension store: 12 JS source plugins | YouTube Music InnerTube search/home, Deezer enrichment, URL import, matching heuristics, minimal plugin contract |

Out of scope, skipped on purpose: paid-catalog downloads through Mornye's HMAC gateway (Deezer/Qobuz/Tidal/Amazon DRM), Spotify TOTP auth, video features, native Android code (widgets, bit-perfect USB audio, foreground services), limusic's own cipher/PoToken/BotGuard stack (yt-dlp already covers it).

Fit: **Direct** = drop-in idea, **Adaptable** = same idea, different mechanics, **Stretch** = new direction. Effort: S < 1 day, M a few days, L a week+.

---

## Tier 1. Keep pipeline quality (all Direct, all S)

These change what lands in Navidrome. Highest value per hour.

### 1.1 Proper artist / album / date / track tags
Source: ytdlnis `YTDLPUtil.kt` ~L1120-1160 (parse-metadata rules), Sealplus `DownloadUtil.kt:990-1062`.
Today Reef writes whatever `FFmpegMetadata` gets: artist = uploader, often "Artist - Topic". Both apps rely on yt-dlp's music fields (`artists`, `artist`, `creators`, `album`, `release_year`, `track_number`), populated for YouTube Music / "- Topic" uploads, and strip the `- Topic` suffix. Neither splits "Artist - Title" strings; Reef could add that as a last fallback.

```python
# downloader.py, before FFmpegMetadata
{"key": "MetadataParser", "when": "pre_process", "actions": [
    (I, "%(artists,artist,creators,uploader,channel|)l", r"^(?P<uploader>.*?)(?:(?= - Topic)|$)"),
    (I, "%(playlist_uploader,artist,uploader|)s", r"^(?P<first_artist>.*?)(?:(?=,\s+)|$)"),
    (I, "%(album,playlist_title,playlist|)s", "%(meta_album)s"),
    (I, "%(album_artist,first_artist|)s", "%(album_artist)s"),
    (I, "%(release_year,release_date>%Y,upload_date>%Y)s", r"(?P<meta_date>\d+)"),
    (I, "%(track_number,playlist_index)d", r"(?P<track_number>\d+)"),
    (I, "", "(?P<description>)"),  # already in Reef
]}
```

### 1.2 Square cover art
Source: ytdlnis `YTDLPUtil.kt:1342-1348`, Sealplus `DownloadUtil.kt:1047-1051`.
YouTube thumbnails are 16:9; Navidrome shows them letterboxed. Both apps crop to center square via the thumbnail convertor.

```python
"postprocessor_args": {"thumbnailsconvertor": ["-qmin", "1", "-q:v", "1", "-vf",
    "crop='if(gt(ih,iw),iw,ih)':'if(gt(iw,ih),ih,iw)'"]},
"postprocessors": [..., {"key": "FFmpegThumbnailsConvertor", "format": "jpg"}, {"key": "EmbedThumbnail"}]
```
Bonus from Sealplus `ThumbnailUtil.kt:48-158` and limusic `ui/src/lib/thumb.ts`: probe `maxresdefault.jpg` with HEAD, treat Content-Length under ~2000 bytes (or 120x90 size) as the grey placeholder, fall back to `hqdefault.jpg`. Reef's `/api/art` redirect already guesses size; add the probe.

### 1.3 Synced lyrics from LRCLIB, embedded + `.lrc`
Source: ytdlnis `assets/yt_dlp_plugins/lyrics/.../lyrics.py` (227 lines, pure urllib + mutagen, both already Reef deps). Also limusic `src-tauri/src/lyrics.rs:396-560` and Mornye `providers/src/lyrics/lrclib.rs:142-197` for matching rules.
Flow: `GET lrclib.net/api/get?artist_name&track_name&album_name&duration`, then `/api/search` with bracket-stripped title and closest duration within ±5 s; prefer `syncedLyrics`, else `plainLyrics`; write `LYRICS` tag in Opus/FLAC (LRC text), `©lyr` MP4, USLT+SYLT MP3; optional sidecar `.lrc`. Navidrome serves synced lyrics to clients (Symfonium, Amperfy) from the tag. Cache hits forever, misses 24 h (limusic `lyrics_cache`). Port the plugin almost verbatim as `lyrics.py`.

### 1.4 ReplayGain / R128 gain on Keep
Source: Mornye `lib/services/ffmpeg_replaygain.dart:33-97`.
Run `ffmpeg -i f -af ebur128=peak=true:framelog=quiet -f null -` on the finished file, gain = -18 - integrated LUFS, write `R128_TRACK_GAIN` (Opus, Q7.8 integer) or `REPLAYGAIN_TRACK_GAIN`/`_PEAK` with mutagen. Navidrome and Subsonic clients honour it. Fixes loudness jumps between kept tracks and library rips.

### 1.5 SponsorBlock `music_offtopic` removal
Source: ytdlnis `sponsorblock_settings_values`, Sealplus `DownloadUtil.kt:1294-1296,1349-1364`.
Cuts non-music intros/outros from music videos when keeping. Tolerate API failure as success (Sealplus does).

```python
{"key": "SponsorBlock", "categories": ["music_offtopic", "sponsor", "intro", "outro"]},
{"key": "ModifyChapters", "remove_sponsor_segments": ["music_offtopic", "sponsor", "intro", "outro"], "force_keyframes": True},
```
Make it a `reef.env` toggle; default off for safety on live/concert uploads.

### 1.6 Filename template + folder layout
Source: Mornye `core/src/filename/template.rs`, `download_queue_provider_paths.dart:426-488`; ytdlnis default `%(uploader).30B - %(title).170B`.
Reef uses `%(title)s.%(ext)s` flat in `Music/Reef/`. Offer `REEF_OUTTMPL` with default `%(artist|uploader).60B - %(title).120B.%(ext)s` and optional `%(artist)s/%(album)s/` subfolders so Navidrome groups albums. Expose a preview endpoint using `ydl.prepare_filename(info)` (ytdlnis `--print %(filename)s --simulate`).

### 1.7 Retry / back-off profile and fragment concurrency
Source: Sealplus `DownloadUtil.kt:850-858,803-831`, ytdlnis generic options.
```python
"retries": 10, "fragment_retries": 10, "extractor_retries": 3, "file_access_retries": 3,
"retry_sleep_functions": {"http": lambda n: min(120, 2 ** n), "fragment": lambda n: min(60, 2 ** n)},
"socket_timeout": 15, "continuedl": True, "concurrent_fragment_downloads": 4,
```
Phones on cellular/Tailscale drop often; Reef currently fails the job on first error.

### 1.8 Reuse the play-time info dict for Keep
Source: ytdlnis info-json cache (`addWriteInfoJson` L746, reused ≤5 h via `--load-info-json`).
Reef resolves a video for play, then re-extracts it for Keep. Cache the full `info` from `streamer._extract` and feed it to the downloader through `ydl.process_ie_result(info, download=True)`; one fewer YouTube hit per keep, faster, fewer bot checks.

### 1.9 Duplicate guard
Source: ytdlnis `DownloadQueueUtil.kt` (archive / url+type / config modes), Mornye `library_duplicates.dart`.
Before enqueue: id already in `history.json` with existing file, or Navidrome `search3` title+artist match, return 409 with the existing track so the UI shows "already kept, play from library".

---

## Tier 2. Player (web UI)

### 2.1 Queue persistence + resume position — Direct, S
Source: limusic `state.rs persist_queue/restore_queue:3051-3130`, Mornye `playback_session_writer.dart`.
Persist queue, index, position, shuffle snapshot in `localStorage` with a revision counter, throttle position writes to every 5 s, restore paused. Already on Reef's roadmap.

### 2.2 Sleep timer — Direct, S
Source: Mornye `music_player_service.dart:678-695`. Duration or end-of-track. Pure JS.

### 2.3 Media Session API — Direct, S
limusic/Mornye use MPRIS / audio_service for lock-screen controls. PWA equivalent: `navigator.mediaSession` metadata + action handlers (play, pause, next, prev, seek). iOS Safari supports it. Cheap, big felt improvement.

### 2.4 Queue semantics — Direct, S
Source: limusic `state.rs:4109-4470`. Play-next FIFO vs add-to-queue tail, shuffle only upcoming and un-shuffle restores snapshot, repeat off/all/one, dedupe toggle. Reef has shuffle/repeat; add play-next and persistent "played" history (limusic keeps 200 rows).

### 2.5 Synced lyrics view — Direct, M
Source: limusic `LyricsView.svelte`, Mornye `lyrics_parser.dart:142-183`.
Server: `/api/lyrics/<id>` returning LRC (from Tier 1.3 cache, or on-demand LRCLIB for un-kept tracks). Client: parse `[mm:ss.xx]` lines, highlight on `timeupdate`, click to seek, auto-scroll. Enhanced-LRC word tags optional later.

### 2.6 Loudness normalization at play time — Adaptable, S-M
Source: limusic `state.rs loudness_gain` (target -7 dB relative to YouTube's 14 LUFS reference, clamp -24..+3), Mornye `playback_normalization.dart` (ReplayGain tags, -18 LUFS).
For kept tracks read gain from Tier 1.4 tags via Subsonic `replayGain`; for YouTube streams yt-dlp does not expose `loudnessDb` reliably, so use a Web Audio `GainNode` with a per-track gain from an `ebur128` scan is too slow. Pragmatic: normalize kept tracks only.

### 2.7 Crossfade — Adaptable, M
Source: limusic second mpv deck with cosine fade, Mornye `music_playback_deck.dart` 40 ms ticks.
Two `<audio>` elements with a gain ramp over the last N seconds. Works for Navidrome streams; works for YouTube proxy streams only if the next URL is prefetched (Reef already prefetches). Beat-matching (Mornye AutoMix) is Stretch.

### 2.8 Thumbnail sizing helper — Direct, S
Source: limusic `ui/src/lib/thumb.ts`. Pick `mqdefault/hqdefault/maxresdefault` per slot, detect the 120x90 placeholder via `naturalWidth`, fall back. Reef's `fallbackImg` does half of this.

### 2.9 Play stats and personal shelves — Direct, M
Source: limusic `stats.rs` (On Repeat = top 20 by plays in 30 days, Familiar Artists), Mornye `listening_statistics.dart:121-197` (count a play after 30 s or half), `listening_rewind.dart`.
Reef has "Jump back in" from recent plays. Add `POST /api/listen` at the 30 s mark into a `listens.json`, derive On Repeat, top artists, streaks. For kept tracks Navidrome already scrobbles if Reef plays them via Navidrome (roadmap `/api/nd-stream`).

---

## Tier 3. Discovery: YouTube Music InnerTube instead of flat `ytsearch`

Reef's search is `ytsearch10` flat extraction: mixed videos, 16:9 thumbs, no album/artist structure. Three of the five apps talk to `music.youtube.com/youtubei/v1` directly with the WEB_REMIX client. One `requests` module, no auth, anonymous `visitorData`.

### 3.1 YT Music search with song/video/album/artist filters — Direct, M
Source: Mornye-Extension `sources/ytmusic-spotiflac/index.js`, limusic `crates/innertube/src/endpoints.rs:24-31,139-224` (filter params: songs `EgWKAQIIAWoKEAkQBRAKEAMQBA%3D%3D`, plus video/album/artist/playlist constants), parsers in `models/metadata.rs`.
Returns `videoId`, title, artists with channel ids, album with `MPRE…` browse id, duration, square thumbnails, explicit badge. Keep yt-dlp for the stream itself. Cheapest first step: yt-dlp `default_search="https://music.youtube.com/search?q="` (ytdlnis L135), which already yields `artists`/`album`/`release_year` fields, before writing an InnerTube client.

### 3.2 Search suggestions — Direct, S
Source: limusic `music/get_search_suggestions` (sent anonymously so it does not pollute history); ytdlnis uses `suggestqueries.google.com/complete/search?client=youtube&ds=yt&client=firefox&q=` (`GoogleApiUtil.kt`), no InnerTube needed. Debounce 300 ms. Reef already stores recent searches.

### 3.3 Song radio / autoplay — Direct, M
Source: limusic `state.rs start_radio / extend_queue_radio / fetch_radio` (~L308-1340), `metadata.rs:306-316`.
`POST youtubei/v1/next` with `playlistId=RDAMVM<videoId>` gives a 20-50 track mix; a bare `next(videoId)` exposes `automixPreviewVideoRenderer` for the real mix id when radio is dead. Autoplay: when ≤2 tracks remain, call `next(last, seed)`, dedupe against the whole queue, append. Also yields `counterpart` (song↔video twin) and `lyrics_browse_id`. This is the single biggest UX upgrade over "search, play one, silence".

### 3.4 Home feed, real moods & genres — Adaptable, M
Source: limusic `endpoints.rs:510-575,676-682`, `models/home_shelves.rs`, `commands.rs get_mood_art`; Mornye-Extension `getHomeFeed` for `FEmusic_home` with `gl` country.
`browse FEmusic_home` returns Quick picks, Mixed for you (anonymous = generic), charts; `FEmusic_moods_and_genres` returns the real tile catalogue with `params`; tiles have no art, so fetch the category's first playlist thumbnail and cache 7 days. Replaces Reef's hand-coded `tiles.py` queries with YouTube's own, while keeping the daily cache pattern.

### 3.5 Album / artist / playlist pages — Adaptable, M
Source: limusic `endpoints.rs:753-912` (album `MPRE…`, OMV rows swapped for `OLAK5uy_` audio playlist ids, artist `UC…`, playlist `VL…`).
"Keep whole album" becomes possible: browse album, enqueue each track with `track_number`/`album` from Tier 1.1 rules. Pair with 4.3.

### 3.6 Open a pasted YouTube / YT Music link — Direct, S
Source: ytdlnis `ResultRepository.getQueryType` + `Extensions.kt:648-729` regexes; limusic `ui/src/lib/ytlink.ts`; Mornye `share_intent_service.dart`.
Search box accepts `watch?v=`, `youtu.be/`, `music.youtube.com/...`, `playlist?list=`, `/@channel`. Add PWA `share_target` in `manifest.json` so the Android/iOS share sheet sends links straight to Reef (Android installs of the PWA get it; iOS Safari does not, so keep paste).

### 3.7 Blocked artists / hide music videos — Direct, S
Source: limusic `crates/innertube/src/blocklist.rs`. Filter by channel id or normalized name at the parse boundary; "prefer songs over videos" toggle picks the `counterpart` song id.

---

## Tier 4. Library, Navidrome, import

### 4.1 Metadata enrichment before tagging — Adaptable, M
Source: Mornye-Extension `sources/deezer/index.js:512-556,1150-1159` (used by four extensions), Mornye `providers/src/deezer.rs`, `providers/src/musicbrainz/http.rs:72-76`.
Deezer public API, no auth: `api.deezer.com/search?q=artist:"x" track:"y"` returns ISRC, album, track number, release date, 1400 px square cover; `api.deezer.com/track/isrc:<isrc>` for exact lookups; MusicBrainz `ws/2/recording?query=isrc:...&inc=tags+releases+artist-credits` for genre and album artist (1 req/s). Apply the matching score from 4.2 and fill only empty tags; swap the YouTube thumbnail for the Deezer cover when confident. Turns YouTube rips into properly tagged library entries.

### 4.2 Track matching heuristics — Direct, S
Source: limusic `import.rs:181-230,297-440` (title Sørensen-Dice, artist Jaro-Winkler, duration ±3 s free then penalty past 20 s, live/remix version-word penalty), Mornye-Extension `tests/provider_metadata_matching.test.cjs`, Mornye `core/src/matching.rs`.
One `matching.py` used by enrichment, duplicate guard, and playlist import.

### 4.3 Album / playlist Keep with selection — Adaptable, M
Source: Sealplus `PlaylistSelectionPage` + `TaskFactory.kt:94-120`, ytdlnis `DownloadMultipleBottomSheetDialog.kt`, playlist-as-album tagging.
`extract_flat="in_playlist"` probe, selection list in UI, one job per item with `playlist_index` as track number and playlist title as album.

### 4.4 Playlist import from Spotify / CSV / M3U — Adaptable, L
Source: limusic `spotify.rs` + `import.rs` (embed page / export zip / CSV), Mornye `csv_import_service.dart:134-222` (ISRC first, then "artist title"), `m3u_playlist_service.dart`.
Upload CSV or paste a public Spotify/Deezer/Apple album or playlist URL, resolve the tracklist via public metadata (Deezer needs no auth; Tidal public `tidal.com/v1` with static token; Apple catalog with scraped dev token), `ytmsearch1` per row, score with 4.2, batch Keep. Direct path to "move my playlists into Navidrome". Skip the Spotify TOTP path; use Deezer's API or the Spotify embed page only.

### 4.5 Open on other platforms / ISRC lookup — Adaptable, S
Source: Mornye `resolver/client.rs:27-36,98-263` (song.link scrape → `api.unitune.art` → MusicBrainz → `squigly.link`).
A song.link/odesli query from the YouTube URL yields the ISRC and store links; feeds 4.1 cheaply.

### 4.6 Observe sources: auto-keep new uploads — Adaptable, M
Source: ytdlnis `ObserveSourceWorker.kt`, `ObserveSourcesItem.kt`, `ObserveAlarmScheduler.kt`.
Follow a channel or playlist; a background thread flat-fetches every N hours, diffs against `alreadyProcessedLinks`, "only new uploads" marks existing items as ignored on first run, optional sync deletes removed items. Reef's tile refresher thread is the template. Great for "keep everything this label posts".

### 4.7 Wishlist / loved — Direct, S
Source: Mornye `loved_tracks`, `wishlist_tracks`. Star a search result to keep later; "Keep all wishlist" button.

### 4.8 Weekly releases for followed artists — Stretch, M
Source: Mornye `weekly_releases.dart`. Needs a release source: MusicBrainz `release?artist=`, or YT channel RSS `youtube.com/feeds/videos.xml?channel_id=` (no auth, cheap). Pairs with 4.6.

---

## Tier 5. Robustness and operations

### 5.1 Stream URL cache rules — Direct, S
Source: limusic `state.rs resolve / cache_horizon`, `orchestrator.rs:712-750`.
Reef caches resolved URLs for a fixed 3600 s. Better: read `expire=` from the googlevideo URL, treat cached as usable only if remaining life ≥ track duration + 60 s, validate with a tail `Range: bytes=<len-256>-<len-1>` probe (catches URLs capped at 1 MiB by some clients), evict on 403 and re-resolve once.

### 5.2 Bounded-range chunking in the proxy — Adaptable, S
Source: limusic `src-tauri/src/audioproxy.rs`. googlevideo throttles open-ended `Range: bytes=X-` to ~2x realtime; re-issue upstream in 4 MiB bounded chunks, 20 s stall guard. Only needed if seeking/buffering stalls are observed; measure first with `scripts/bench_resolve.py`.

### 5.3 Persistent job queue with states, auto-retry, Retry all — Adaptable, M
Source: Sealplus `DownloaderV2.kt` (Idle→FetchingInfo→Running→Completed/Paused/Error, network-keyword retry 3x5 s, grace-period pause on network loss, queue serialized on state change), ytdlnis `Errored` tab + `reQueueDownloadItems`.
Reef's `_jobs` dict dies with the process; Termux restarts are common. Persist `queue.json`, resume on boot, retry on `Unable to connect / Connection reset / timed out / HTTP Error 5 / SSLError`, keep failed queries with their error for one-tap retry.

### 5.4 Cookies, PO tokens, player client config — Adaptable, M
Source: ytdlnis `setYoutubeExtractorArgs` L801 + `BgUtilsPoTokenGeneratorUtil.kt`, Sealplus `parseCookieContent` (Netscape / Cookie-Editor JSON / header string).
When YouTube starts answering "Sign in to confirm you're not a bot": a settings page textarea that accepts any of the three cookie formats, written to `cookies.txt` and passed as `cookiefile`; `REEF_YT_PO_TOKEN` passthrough to `extractor_args["youtube"]["po_token"]`; optional `bgutil-ytdlp-pot-provider` in Termux. Reef already has `REEF_YT_CLIENT`.

### 5.5 Smarter error surfacing — Direct, S
Source: negative lesson from ytdlnis/Sealplus (raw stderr). Map common yt-dlp messages (bot check, age gate, geo, private, removed, 403 on download) to short UI hints with the fix (update yt-dlp, add cookies, switch client). Reef already truncates errors to 300 chars; classify them.

### 5.6 yt-dlp update from the UI — Adaptable, S
Source: Sealplus `UpdateUtil.kt:64-83`, ytdlnis `YTDLUpdater.kt` (stable/nightly/master).
Button on the health page: `pip install -U yt-dlp` (or `--pre` nightly) then re-exec. Show version diff. Most "search broke" incidents end here.

### 5.7 Per-keep log — Direct, S
Source: ytdlnis `LogItem` (rendered command + output). Capture yt-dlp `logger` per job into `logs/<job>.txt`, link from the Downloads tab.

### 5.8 Live health smoke test — Adaptable, S
Source: limusic `.github/workflows/stream-health.yml`, `crates/innertube/tests/live_smoke.rs`. A `scripts/smoke.py` that searches, resolves, and byte-probes one known id; run from the tile refresher daily and surface in `/api/health`.

### 5.9 Backup / restore — Direct, S
Source: Mornye `backup_service.dart`, ytdlnis `BackupSettingsUtil.kt`. Zip of `history.json`, `tiles.json`, `queue.json`, lyrics cache, `reef.env` minus secrets. Reinstalling Termux loses everything today.

---

## Tier 6. Stretch and innovation

| Idea | Source | Why it might fit | Effort |
|---|---|---|---|
| Mini source-plugin system | Mornye-Extension manifest contract | Python modules in `sources/` exposing `search / resolve_stream / home / enrich / handle_url` over a shared `Track` dataclass; enables SoundCloud, JioSaavn, Bandcamp later without touching core | M |
| SoundCloud source | Mornye-Extension `sources/soundcloud` (client_id scrape, `api-v2`, HLS → ffmpeg remux); yt-dlp also has `scsearch` | Remixes/mixes missing from YouTube; yt-dlp's extractor makes this a one-line search prefix | S via yt-dlp, M native |
| Listen Together | limusic `crates/listen-protocol` | Host-authoritative WebSocket room; Reef is already a server, so a shared queue between two phones on the tailnet is natural | L |
| Lyrics share card | Mornye `lyrics_share_screen.dart` | Canvas render of 5 lines + cover, 1080x1920 | M |
| Autoplay personalization | Mornye `autoplay_personalization.dart` (online logistic regression on hashed features, skips < 30 s as negatives) | Rank radio candidates by local listens; overkill until 2.9 exists | L |
| Hi-res authenticity check | Mornye `core/src/media/hires.rs` | Not relevant: YouTube is lossy by definition | skip |
| Equalizer | Mornye / limusic | Web Audio `BiquadFilterNode` chain; iOS Safari Web Audio + `<audio>` interplay is fragile | M |
| Terminal / custom yt-dlp templates | ytdlnis, Sealplus `--config-locations` | Reef lives in Termux already; a `REEF_YDL_EXTRA` JSON merged into `ydl_opts` covers power users | S |

---

## Suggested order

1. Tier 1.1, 1.2, 1.7, 1.9 in one pass over `downloader.py` (one day, visible in Navidrome immediately).
2. 1.3 lyrics + 2.5 lyrics view, 1.4 ReplayGain.
3. 2.1 queue persistence, 2.2 sleep timer, 2.3 Media Session.
4. 3.1 via yt-dlp `ytmsearch` first, then 3.3 song radio (first InnerTube call), then 3.4 home feed.
5. 5.3 persistent queue, 5.1 URL cache rules, 5.6 update button.
6. 4.1 enrichment + 4.2 matching, then 4.3 album keep, 4.6 observe sources, 4.4 import.
