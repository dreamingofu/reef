# Reef UI remake plan

One combined design that takes the best UI ideas from seven open-source music apps and rebuilds them in Reef's
vanilla HTML/CSS/JS for iPhone Safari and the Home Screen app. Compiled 2026-10-21 from their screenshots and UI
code, on top of Reef at `dc09f95` (Octave-style shell, lyrics view, Up next, sleep timer, YouTube Music browse).

| App | UI personality | What we take |
|---|---|---|
| **Metrolist** (Compose) | Material 3 Expressive on pure black | Mini player that morphs into Now Playing, squishy transport, Quick picks grid, swipe-to-queue rows |
| **ArchiveTune** (Compose) | Most polished, close to Apple Music | Immersive full-bleed player, lyrics depth/word fill, full-bleed artist hero, library mix, stats |
| **Meld** (Metrolist fork) | Same visuals as Metrolist | "Fix wrong match" dialog |
| **Flow** (Compose) | Expressive, cover-coloured everything, spring motion | Gesture system, stretchy controls, seek bar, joined action row, speed dial, recap stories, menu sheet |
| **Gyawun** (Flutter) | YT Music clone, calm grouped lists | Joined list blocks, play button shape morph, cover-tinted gradient, Play-all split button |
| **Musify** (Flutter) | Tonal "cute" shapes | Wavy progress ring, quality badge, playlist header with sort/filter, Time Machine recap |
| **BloomeeTunes** (Flutter) | Plum black, pink/blue | Ambient background, Up next peek panel, glass mini card, chart pages, blur-fill covers, track menu card |

Paths in **Source** cells are relative to each repo; all seven are GPL, so their code is reference only: Reef
re-implements the designs and numbers from scratch (MIT). No assets, icons or font files are copied.

Priority: **P0** defines the remake, **P1** strong, **P2** nice. Effort for vanilla JS: S < 1 day, M a few days, L a
week+. "Needs" marks a backend addition.

---

## 0. Platform rules (read first)

These come from all four UI surveys and decide how several features are built.

- **Serve over HTTPS.** `http://100.78.71.110:5274` is not a secure context, so iOS blocks `navigator.share({files})`,
  clipboard writes, Wake Lock and the microphone. Run `tailscale serve --bg --https=443 http://127.0.0.1:5274` (or
  `tailscale cert`) and use `https://<phone>.<tailnet>.ts.net`. Needed by: share cards, copy actions, bedside mode,
  song recognition.
- **No Web Audio in the playback path.** Routing `<audio>` through Web Audio (EQ, gain, visualiser) stops playback on a
  locked iPhone, and iOS ignores `audio.volume`. So: no equaliser, no volume slider, no real-time visualiser. Show
  AirPlay via `audio.webkitShowPlaybackTargetPicker()` instead of a volume bar.
- **Blur budget.** Never animate `filter: blur` or stack `backdrop-filter` on large areas. Blurred backgrounds use a
  tiny copy of the art (`/api/art/<id>?size=64`) scaled up with a static `filter: blur(30px)`; cross-fade two layers
  instead of re-blurring.
- **Edge swipe.** Ignore horizontal drags that start within 24px of the left edge, so Safari's back-swipe still works.
- **Gestures.** Pointer events + `touch-action: none` on draggable surfaces, `overscroll-behavior: contain` in sheets,
  `-webkit-touch-callout: none` where long-press is used. No haptics (no `navigator.vibrate` on iOS).
- **Springs.** CSS `linear()` easing (Safari 17.2+) approximates the apps' springs; keep `--ease:
  cubic-bezier(.32,.72,0,1)` for no-bounce settles and `cubic-bezier(.34,1.56,.64,1)` for the squishy ones.
- **Colour from artwork** must sample `/api/art` (same origin) so the canvas isn't tainted. View Transitions exist on
  Safari 18+; fall back to FLIP with `getBoundingClientRect`.
- `prefers-reduced-motion`: drop the stagger, word pops, wave and count-ups; keep fades.

---

## 1. Design system

### 1.1 Colour (P0, S)
- **Surfaces, AMOLED ramp** (Flow `theme/PaletteColors.kt:60-65`, Musify `theme/app_themes.dart:76-98`):
  `--bg #000`, `--s1 #0A0A0A`, `--s2 #121212`, `--s3 #1A1A1A`, `--s4 #1C1C1E`; text `#F5F5F7`, secondary `.72`,
  tertiary `.5`, hairline `rgba(255,255,255,.08)`.
- **Accent**: keep Reef pink `#FB2C5A` as the default app accent; add an accent picker (P1, S: 20 swatches in a
  5-column grid, selected one gets a 2.5px ring + glow, 200ms; Gyawun `core/utils/expressive_sheet.dart:80-200`).
- **Cover palette** (P0, M): the mini player, Now Playing, lyrics, album/artist pages and the top search result take
  their colours from the artwork; app chrome keeps the accent.
  - Draw `/api/art` into a 32-128px canvas; bucket pixels; rank by population with ×1.3 for vivid colours
    (saturation > .3, brightness .2-.9); treat the image as grey if mean saturation < .22 (ArchiveTune
    `ui/theme/PlayerColorExtractor.kt:30-143`).
  - Roles (Flow `MusicPlayerTheme.kt:91-137`, `MediaPalette.kt:61-116`): `--c-base` darkest muted colour clamped to
    lightness ≤ 26%; `--c-accent` most vibrant at ~80% lightness with ≥ 4.5:1 contrast on base; `--c-on-accent` ~20%;
    `--c-tonal` = `color-mix(in oklab, var(--c-base), white 16%)`.
  - Cache per track id in memory + `localStorage`; transition custom properties over 800ms (register them with
    `@property` so they animate).
  - Setting: "Cover colours / Accent only / Pure black".

### 1.2 Type (P1, S)
System stack (SF Pro on iPhone), `letter-spacing: -0.011em` for display sizes. Scale (Flow): 34/28 bold display,
24/20 semibold titles, 18/16/14 medium labels, 16/14/12 regular body. Tabular figures for times, ranks and counters.
Optional display face for big numbers: Rethink Sans from Google Fonts (BloomeeTunes uses it).

### 1.3 Shape (P0, S)
- Radii: 8 small art, 12 cards/tiles, 16 sheets' inner cards, 20-24 big cards, 28-32 hero art and pills, 50% circles.
- **Joined groups** (Gyawun `expressive_list_group.dart`, Flow `MusicShelf.kt:144-181`): consecutive rows in a block
  get 2px gaps, outer corners 16-20px, inner corners 4px. Used for lists, settings, quick picks, action rows.
- **Cookie shape** for artist portraits and the recognition button: 8-9 lobe SVG clip-path in
  `objectBoundingBox` units (Flow `FlowShapes.kt:21`, Musify `playlist_hero_artwork.dart`). P1.

### 1.4 Motion tokens (P0, S)
| Token | Value | Use | Source |
|---|---|---|---|
| press | scale .96-.98 + opacity .85, 100-120ms | cards, rows, buttons | BloomeeTunes `square_card.dart`, Musify mini player |
| tab | fade + scale .96→1, 250ms | switching views | BloomeeTunes `global_footer.dart:157-172` |
| sheet | 300-380ms `--ease`, no fade of the page | Now Playing, sheets | BloomeeTunes `player_overlay_wrapper.dart:40-47` |
| list-in | each row fades + rises 16px, 250ms, +30ms per row (cap 200ms) | first render of lists | BloomeeTunes `animated_list_item.dart` |
| cross | 300ms cross-fade | titles and art on track change | Musify, Metrolist |
| colour | 800ms | cover palette changes | ArchiveTune, Flow |
| skeleton | opacity .25→.5→.25 over 800ms after a 250ms delay, fading downward | loading | Metrolist `shimmer/ShimmerHost.kt:56-73` |

### 1.5 Empty and error states (P1, S)
40px icon + 14px text at .7, one action button ("Search", "Retry"). Errors use the typed reasons from
`docs/PRODUCTION_PLAN.md` P0.4 ("YouTube wants a bot check: try mobile data").

---

## 2. App shell and navigation

### 2.1 Tabs (P0, S)
Keep the glass nav pill + round search FAB (Reef/Octave). Tabs become **Home · Library · Search FAB**:
Kept and Downloads move into the Library hub (§6), which frees the pill for a third tab later (Charts or Stats).
The header's right side gets an avatar circle that opens **Settings** (§9.1); the status dot moves into Settings.

### 2.2 Wide layout (P1, M) — iPad / landscape
At ≥ 680px: Now Playing puts art (≤ 48% width, max 640) beside controls (max 420); a queue column 320/370/420px at the
800/1100/1240 breakpoints toggles in 280ms. Album pages use a fixed left column (35%, ≥ 340px) and a scrolling track
list in a glass panel (radius 24). At ≥ 1000px the nav pill becomes a 70px side rail. (BloomeeTunes
`desktop_player_layout.dart`, `player_screen.dart:186-243`, `playlist_screen.dart:276-381`.)

---

## 3. Mini player

### 3.1 Layout (P0, M) — Flow + ArchiveTune + BloomeeTunes
- Card 64px tall, 12px side margins, max width 420px, centred. Radius 32 when floating; when docked above the nav
  pill 28 top / 12 bottom with a 4px gap (ArchiveTune `constants/Dimensions.kt:22-30`).
- Background: `--c-base` from the cover palette under a glass layer (`rgba(0,0,0,.45-.6)` + 0.5px border at .06 and a
  .15 top highlight; BloomeeTunes `mini_player_widget.dart:173-251`).
- **Progress ring around round art**: 43px circular art inside a 52px SVG ring, 2.5px stroke, round cap, starting at
  12 o'clock, driven by `stroke-dashoffset` (Flow `MiniPlayerContent.kt:66-250`, Metrolist/ArchiveTune same idea).
  Replaces the hairline bar.
- While playing, the art gets a black .46 layer with three equaliser bars (2.5px wide, 1.5px gap, 350ms cycle,
  120ms stagger).
- Title 14-15px/600, marquee every 2.5s when long; artist 12-13px at .72; both cross-fade on track change.
- Buttons: play 48px (white .92, black icon), next 48px tonal. Optional prev.
- Enter: slide up 1.5× its height + fade, 350ms.

### 3.2 Gestures (P0, S-M) — Flow numbers
- Horizontal drag: first 100px moves at most 30px (resistance), then 1:1. Commit at ⅓ width or 1000px/s; the card
  exits, then re-enters with the new track. Behind it, show the next/previous track's art + title, or
  "Nothing up next" (Flow `MediaMiniBarSwipe.kt:30-180`).
- Swipe up or tap opens Now Playing; pull down > 32px or 800px/s pauses and hides it.

### 3.3 Morph into Now Playing (P1, L) — Metrolist + Flow
One element with progress `p` 0 (mini) → 1 (full): width/left/height interpolate to full screen, radius `32·(1-p)`,
mini content opacity `1-2p`, full content opacity `(p-.25)/.75` with a 24px rise, backdrop `1.4·√(max(p,.1)-.1)`.
Release: 150px/s or 5px decides direction, else snap at halfway; settle with a no-bounce spring (~380ms). A drag down
on an inner list already at scroll-top collapses the sheet (Metrolist `ui/component/BottomSheet.kt:71-326`, Flow
`UnifiedMusicPlayerSheet.kt:263-443`). Until this ships, keep today's slide-up sheet with a FLIP of the art.

### 3.4 Variant (P2, S)
Musify's wavy ring around the play button: SVG path `r + 1.5·sin(12θ)`, stroke 3, `pathLength=1`
(`widgets/mini_player.dart:401-521`). Setting: ring style Classic / Wavy.

---

## 4. Now Playing

### 4.1 Background styles (P0, M) — setting with live preview
1. **Ambient** (default; BloomeeTunes `artwork_ambient_background.dart:25-54`, Flow `PlayerBackground.kt:41-208`):
   blurred 64px art at .55 over black, then a gradient: 0% black .40 → 30% `--c-accent` .16 → 55% `--c-base` .27 →
   80% black .80 → 100% black .95; cross-fade 450-800ms on track change.
2. **Immersive** (ArchiveTune `ui/player/immersive/ImmersivePlayerScreen.kt:214-544`): full-width art 64vh tall,
   masked `linear-gradient(#000 0 44%, rgba(0,0,0,.96) 58%, rgba(0,0,0,.7) 70%, rgba(0,0,0,.28) 82%, transparent)`,
   over a mirrored (`rotate(180deg)`) blurred copy with a black 0→.40 scrim; controls start at 40-46% of the height,
   28px side padding.
3. **Tint** (Gyawun `player_page.dart:90-161`): 135° gradient from `--c-base` at .78 to .31 over black.
4. **Pure black.**

### 4.2 Layout (P0, S) — Flow compact layout + Reef
- Top bar: chevron-down, centre "NOW PLAYING" (16px bold, +1px tracking, .9) over "Playing from <queue source>"
  (14px, .7, tappable → that album/playlist/search), right ⋯ (opens the track menu, §8.1).
- Art: `min(width − 56, height left after controls)`, radius 12-20, shadow 24 playing / 8 paused, scale .96 when
  paused (Reef already sizes by height). **Blur-fill** for non-square YouTube art: the same image blurred (σ40) under
  black .30 behind the fitted art (BloomeeTunes `playlist_screen.dart:447-492`).
- **Quality badge** on the art, bottom-right 10px: "FLAC · 16/44.1", "AAC · 128k", "Opus" — black .55, radius 8,
  11px/600 (Musify `now_playing_artwork.dart:280-293`). Needs: stream resolver exposes codec/bitrate (it knows them).
- Title 22-24px bold, marquee after 3s at 30px/s; artist 16px .75, tap → artist page; long-press either → copy
  "Artist – Title" (Musify `now_playing_controls.dart:95-118`).
- Right of the title: **joined capsule** Like | Keep (each 50×42, gap 6, outer radius 50%, inner 6; Keep shows a
  download ring while keeping; long-press Like → Add to playlist) (Flow `PlayerSecondaryActions.kt:170-271`,
  Metrolist share/like pair).
- Vertical rhythm: 16 · title · 24 · seek · 26 · transport · 18 · action row · queue peek.

### 4.3 Art gestures (P0-P1)
- **Swipe art to skip** (P0, M): neighbours sit at ±width; commit past 30% width (200ms ease-out), else spring back;
  resistance .3 with no neighbour (Flow `full/PlayerArtwork.kt:94-246`). Alternative built on CSS scroll-snap with a
  120ms scroll-end debounce (Metrolist `ThumbnailSnapUtils.kt:29-58`) — pick one; scroll-snap gets iOS momentum free.
- **Double-tap seek** (P1, S): left/right half ±5s, each extra tap within 1s adds 5s, small bubble shows "+15s"
  (Metrolist `Thumbnail.kt:528-553`). `touch-action: manipulation`.
- **Tap art → lyrics** (P2): Musify flips the card (800ms Y-axis flip, `-webkit-backface-visibility:hidden`); Reef
  opens full lyrics instead (§5), the flip is an option in Settings.

### 4.4 Seek bar (P0, S) — Musify/Flow "2024" slider
Custom div slider (Safari can't style `<input type=range>` enough): track 12-16px, 22px while dragging; played part
`--c-accent`, rest accent .24; thumb a 4×36 vertical bar with a 6px gap either side; 4px stop dot at the end. Times
11-15px below: elapsed left, remaining "-1:03" right; elapsed turns bold white while scrubbing; centre pill shows
source/quality (exists). Option: **squiggly** played segment (SVG wave ~30px wavelength, 2.5px amplitude, 5px stroke,
drifting 9px/s, flattens in 150ms when paused/scrubbing; Metrolist `SquigglySlider.kt:73-117`).

### 4.5 Transport (P0, S) — Metrolist/Flow stretchy row
Three pills in a 68px row, gap 10, `flex-grow` .45 / 1.3 / .45. Pressing play → 1.9 and sides .35; pressing a side
→ .65 and play 1.1; 280-350ms `cubic-bezier(.34,1.56,.64,1)`. Play: accent fill, 36px icon, radius 18 playing ↔ 34
paused (Gyawun's circle↔rounded-square morph, 200ms) with the icon morphing; prev/next 30px icons on tonal fill.
Skip fires 180ms after tap so the squish is visible. Long-press prev/next previews the neighbour (its art slides in,
title swaps) (P2; Flow `MediaPlaybackControls.kt:134-245`). Option: Bloomee's play-state colour (paused = accent,
playing = secondary colour with glow, 200ms).

### 4.6 Action row (P0, S) — Flow joined toggles
One 42px row: Lyrics | Shuffle | Repeat | Queue | Sleep, gap 4, outer radius 21, inner 8; an active toggle becomes a
full pill in `--c-accent`, inactive white .08; separate round 42px ⋯ at the end. Sleep shows remaining minutes when
set. AirPlay button left of the row (§0). Replaces today's bottom row.

### 4.7 Queue peek + pull-up (P0, M) — BloomeeTunes + Gyawun + Flow
- A 60px "Up next · <next title>" strip at the bottom (radius 25 top, `#20202A→#0E1018` or cover tonal, 0.5px top
  border .1, 50×5 grabber) (BloomeeTunes `up_next_panel.dart:26,178-206`, Gyawun `player_page.dart:274-361`).
- Drag up opens the queue over the player: opens on velocity < −520px/s or drag past clamp(5% height, 14, 56); closes
  on > 450px/s; the player fades `1−f/.4`, the art scales `1−.1f`, sheet radius 28→0 (Flow
  `QueuePullUpSheet.kt:116-213`). Queue contents in §7.

---

## 5. Lyrics (P0, M-L)

Full-screen view opened from the action row (and by tapping the art).
- Background: `--c-base` mixed 30% toward black (solid, cheap) with the ambient art option.
- Text: accent-tinted white, 26-36px weight 600 (active 800), auto-shrink long lines (>42 chars ×.9, >54 ×.8,
  >72 ×.72, >92 ×.66) (Flow `LyricsLine.kt`).
- Active line anchored at 35% of the viewport height; padding 35% above / 60% below; auto-scroll 280ms.
- Depth by distance from the active line (ArchiveTune `LyricsV2.kt:184,293,549-617`): opacity 1 / .52 / .30 / .18 /
  .10; scale 1.05 active, .95 others (166ms); blur 0 / 2 / 5px for ±2 lines only (iOS budget), none beyond;
  opacity in 330ms / out 500ms `cubic-bezier(.4,0,.2,1)`. When the line changes, rows move in a 20ms stagger (≤ 200ms).
- **Line bounce** (P1, S): on activation each word pops in turn (40ms stagger, scale 1.045, −5px, springy).
- **Word fill** (P2, M): `background-clip:text` gradient with a ~0.9em soft edge sweeping each word, +3px lift, glow
  on words held > 1.1s. LRCLIB is line-timed, so estimate word timing by spreading the line's duration over its
  syllables; use real word timing if a word-synced provider lands (PRODUCTION_PLAN P1.3).
- **Interlude dots**: gaps > 4s show three dots filling in turn, 1.6s pulse, fading 650ms before the next line.
- Manual scroll pauses auto-follow for 3-4s and shows a "Resync" pill (radius 28, black .42); tap a line to seek.
- Plain (unsynced) lyrics: 21px, line-height 1.75, normal scrolling.
- Chrome: top pill with 40px round art + title; bottom: back 48 · play 64 (radius 18↔32) · ⋯ 48. "Lyrics only"
  toggle hides chrome (BloomeeTunes).
- Timing nudge ±50ms with tap-to-reset (P2, S). Needs: per-track offset stored with the lyrics cache.
- Open transition: from the bottom, scaleX .92→1, scaleY .78→1, 16% lower start, radius 32→0 (ArchiveTune
  `ui/player/Player.kt:2012-2048`).
- **Share lyrics card** (P2, M): pick lines → canvas card with art + 5 lines (1080×1920) → `navigator.share({files})`
  (needs HTTPS) (Metrolist `LyricsImageCard.kt`).

---

## 6. Library hub (replaces Kept + Downloads tabs)

### 6.1 Hub layout (P0, M) — Flow + ArchiveTune + Gyawun
- Header "Library", search field (filters everything below; song hits say "in <playlist>"; BloomeeTunes
  `library_screen.dart:285-389`).
- Chips (icon + label, selected = accent fill): All · Playlists · Songs · Albums · Artists · Kept · Downloads.
- **Recap card** (opens §9.3) and **Most played** card (radius 32, Play all pill + shuffle circle) (ArchiveTune
  `LibraryMixScreen.kt:202-310`). Needs: play log.
- 2-column tiles: Liked · Kept · History · Top 50 — tinted backgrounds at .6, 36px icon circle, 16px/600 title,
  count, press scale .97 (ArchiveTune `LibraryMixScreen.kt:1042-1070`).
- Then a joined list (Gyawun): Playlists (pinned first, "+ New playlist", folders), Downloads (active jobs), each row
  48px round icon, count, chevron (Flow ss12).
- Grid/list toggle and sort (Recent / A-Z / Artist) persisted per section.

### 6.2 Rows (P0, S)
64-80px rows: 48-70px art (artists circular), title 15-16.5px/600, subtitle .6, pin icon, equaliser bars on the
playing item. Long-press → track menu. Keep progress on the art: dark .42 overlay + spinner while downloading (Musify
`song_bar.dart:553-580`).

### 6.3 New collections (P1, M) — Needs
- **Liked songs**: `/api/likes` store (json) + heart everywhere. Musify, Flow, ArchiveTune.
- **Playlists**: `/api/playlists` CRUD (name, items, pinned, folder), add-to-playlist sheet, reorder by drag. Export to
  `.m3u` in the music folder so Navidrome imports them (Metrolist `PlaylistExporter.kt`).
- **History**: `/api/listens` play log (count a play at 30s or 50%), browsable by day.

---

## 7. Queue (Up next) (P0, M)

Inside the pull-up sheet (§4.7).
- Header: "Playing from <source>", "N songs · 1h 12m", buttons Shuffle · Clear (pill) · Save as playlist. Autoplay /
  "Endless radio" toggle row (exists as Autoplay) (BloomeeTunes, Flow).
- Opening scrolls the current track to 1.5 rows from the top.
- Rows: 6px gaps, 14px side padding, 44-46px art. Current row: radius 30, art radius 22, `--c-tonal` fill (or accent
  .45) with equaliser bars; others radius 14, art 10, black .55 (Flow `QueueTrackRow.kt:122-180`).
- Swipe left → Play next (accent tint), swipe right → Remove (red tint) with an Undo toast; first 60px moves at most
  20px, then 1:1; commit at 40% (Flow `QueueRowSwipeGestureHandler.kt:158-160`).
- Drag handle to reorder: the lifted row gets a shadow (radius 14) (Musify `queue_list_view.dart:318-460`).
  Pointer-event sortable written in-house, or SortableJS (MIT).
- Sections: "Now playing" · "Next in queue" (Play next items) · "Next from <source>" · "Autoplay suggestions" (radio
  extras the user can add, Metrolist).

---

## 8. Sheets and menus

### 8.1 Track menu (P0, M) — BloomeeTunes card + Flow structure + ArchiveTune grid
- Floating card inset 10px, radius 24, page dim black .72, max 85% height, 40×4 grabber + ✕.
- Header: 56px art (radius 10), title 16px/500, artist .7, source badge, Share and Info buttons.
- Quick action grid (3 columns, ≥ 108px tall tiles, 24px icons): Play next · Add to queue · Start radio · Keep · Add
  to playlist · Like (ArchiveTune `GridMenu.kt:48-102`, BloomeeTunes chip row).
- Grouped sections (joined lists, 34px icon boxes radius 12, chevron .22): Go to (Artist, Album) · Recommendations
  (Not interested, Hide artist — exists) · More (Song info, Fix wrong match, Copy link, Open on YouTube).
- Sub-pages slide inside the same sheet with a Back row (artist picker, playlist picker) (Flow
  `QuickActionsSheet.kt:62-334`).
- Long-press any row anywhere opens it.

### 8.2 Row swipe actions (P0, S) — Metrolist
On song rows outside the queue: swipe right → Play next, left → Add to queue at ≈115px, with a 60px coloured band and
icon underneath; commit animates the row back and toasts (Metrolist `Items.kt:1820-1880`). Respect the 24px edge
guard. Setting: choose actions per direction (Flow previews).

### 8.3 Song info (P2, S)
Glass cards: Details (title, artist, album, year, duration, ISRC), Technical (source, codec, bitrate, sample rate,
loudness/ReplayGain, file path for kept tracks), Actions (copy id, copy link, open in browser) (BloomeeTunes
`song_info_screen.dart`).

### 8.4 Fix wrong match (P1, S) — Meld
For tracks matched across sources (Monochrome, imports, lyrics): "Wrong song? Paste the right link" field + top 5
candidates with scores. Needs: `matches.json` with a `manual` flag (PRODUCTION_PLAN P1.6) (Meld
`ui/component/YouTubeMatchDialog.kt`).

### 8.5 Sleep timer (P1, S)
Sheet: chips 15/30/45/60 min/End of track (exists) + Custom with hour/minute steppers (16px rows, 10px −/+ buttons,
20px bold value; Musify `bottom_actions_row.dart:377-600`). While active, a countdown view with big HH:MM:SS boxes
(radius 16, white .07, 40px digits), +5 min and a pink Stop pill (BloomeeTunes `timer_view.dart`).

---

## 9. Home

Order top to bottom. Mood chips and shelves come from YouTube Music browse (exists); personal shelves need the play
log (`/api/listens`).

1. **Header** (P0, S): greeting (exists), avatar → Settings.
2. **Mood chips** (P0, S): horizontally scrolling tonal chips under the header, open the mood page (exists) (Gyawun
   `chips_row.dart`, Metrolist).
3. **Speed dial** (P1, S; needs play counts): 3-row horizontally scrolling grid of 64px tiles, width `(W−32)/2`,
   radius 12, art + 14px bold title + 12px artist; first tile "Shuffle play" in a tertiary colour; the current track
   takes the cover colour + equaliser bars (Flow `SpeedDialSection.kt:51-221`).
4. **Quick picks** (P0, S): 4 rows of 64px per column, columns `W−24−48` (max 360) wide so the next peeks, scroll-snap
   one column, joined blocks (2px gaps, 16/4 radii), art 50px radius 8, title 16/700, subtitle 14/600 .6, ⋯ per row,
   "Play all" outlined pill in the header (Metrolist `HomeScreen.kt:1167,1787-1798`, Gyawun
   `section_multi_column.dart`, Flow `MusicHomeSections.kt`).
5. **Keep listening** (P1, S): 3-column square grid (128px) with a dark round play button on each; artists circular
   (Metrolist).
6. **Charts carousel** (P1, M): cards 65% width × 36% height, centre card enlarged, auto-advance 2.5s; card style
   from BloomeeTunes v2: radius 25, a curved colour band across the bottom 25-35% (`clip-path: path()`) in one of
   four gradients, title right-aligned 28/700 (`bloomee-v2 chart_widget.dart:33-50,162-175`). Opens §10.3.
7. **Shelves** (P0, S): section header 20-22px bold (+ optional subtitle, small accent icon, one trailing action:
   →, Play all, refresh) (Metrolist `NavigationTitle.kt:47-98`); 150px cards radius 12-15, 16:9 for videos,
   circles for artists, title 13-15/700, subtitle 11-13 .62, type pill top-right ("Album", radius 20, 11/700), press
   scale .96; load more within 240px of the end (BloomeeTunes `horizontal_card_view.dart:89-117`).
8. **Artists for you** (P1, S): 84px cookie-shaped portraits (Flow, Musify).
9. **Personal shelves** (P1, M; needs play log with timestamps): On repeat · Your <morning/afternoon/evening/
   late-night> rotation (weekday vs weekend) · Rediscover · Deep cuts (Flow `MusicHomeFeed.kt:119-180`); "Continue
   listening" for long mixes with a progress bar + dismiss ✕ (needs saved positions).
10. **Recently** rail (P2, S): rotated accent label beside paged columns of 3 songs (BloomeeTunes
    `tab_list_widget.dart`).
11. Settings → "Home sections": show/hide/reorder these shelves (Flow).

---

## 10. Search, charts, browse

### 10.1 Search (P0, S-M)
- Bar: pill 50-56px, radius 35 / full, back arrow + ✕; glass (white .05, border .08); the magnifier turns into a
  spinner while loading (Gyawun `search_page.dart:128-160`, BloomeeTunes `search_screen.dart:347-461`).
- Empty field, three sections with 11.5px/700 uppercase headers (+1.1px tracking, .55): **Recent** (clock icon, ✕ to
  delete, long-press too) · **Suggestions** (↖ fills the box without searching; 300ms debounce, stale responses
  dropped) · **Top results** (42px rows with a type pill) (BloomeeTunes `search_screen.dart:842-935,1080-1131`, Musify
  `search_page.dart:284-398`).
- Filter chips: 38px, radius 20, selected solid accent, others white .08, 200ms (exist as chips; restyle).
- **Top result card** (P0, S): radius 28, padding 20, `--c-*` from its art, 104px art (cookie for artists), title
  24 bold; artists get Shuffle + Radio buttons (Flow `TopResultCard.kt:55-210`).
- Results grouped by type (Songs, Artists, Albums, Videos, Playlists), each a joined block with "Show all" (Musify
  `search_page.dart:410+`).
- "No results for 'q'" empty state with "Search Monochrome instead" when the source has none.
- Background glow (P2, S): two radial glows at .18 coloured from the top result, 1.5s transition (BloomeeTunes).
- Voice search (P2): `webkitSpeechRecognition` when present; hidden otherwise (unreliable in Home Screen apps).

### 10.2 Album / playlist page (P0, M) — Flow + Gyawun + Musify + BloomeeTunes
- Page tinted 45% toward the cover's hue, plus a radial `--c-accent` glow at .45 and the art blurred (σ100) at .35
  behind the header (Flow `MusicCollectionColorScheme.kt:20-70`, BloomeeTunes `playlist_screen.dart:218-271`).
- Centred art 55% of width (180-250px), radius 20-28, blur-fill for 16:9 art, shadow in the dominant colour .35
  (blur 60, y 20) + 1px white rim.
- Type label 12px ("Album · 2016"), title 20-36px bold (−1px tracking at large sizes), artist in accent (tap →
  artist), meta "14 songs · 50 min"; description clamped to 2-3 lines with "Show more".
- **Joined Play | Shuffle** (accent | tonal, each half width; Gyawun's Play-all + ⋯ split with radii 24/8) and an icon
  row: Save/Like · Keep album (28px progress ring) · Share · ⋯ · sort on the right (Flow
  `MusicCollectionHeader.kt:105-300`, Musify `playlist_header.dart`).
- Sort chips (Default · Name · Artist) + filter field for long playlists (Musify `sort_chips.dart`).
- Rows in joined blocks with track numbers; playing row tinted accent with equaliser bars.
- Collapsing header: pinned 340px header, title scales 1.35→1; the top bar title fades in after the header scrolls
  away (Musify `playlist_sliver_app_bar.dart`, ArchiveTune).
- Select mode (P2, M): long-press → multi-select → Keep / Add to playlist / Play next.

### 10.3 Artist page (P0, M) — Flow hero + ArchiveTune overlay + Musify shelves
- Hero photo full width, height `min(W, 400) + safe-area top`, bottom radius 48; overlay black .42 top → clear
  18-42% → background .78 at 72% → solid; slight parallax `translateY(scroll × .4)` (Flow
  `ArtistDetailComponents.kt:93-235`, ArchiveTune `MediaDetailHero.kt:96-245`).
- Name 34-36px/800 over the hero's bottom; monthly audience/subscribers line .62.
- Actions: Follow (outline toggle; needs a follow list) · Radio · Shuffle · Play (56px white pill, black bold text).
  The row scrolls sideways with 20px edge fades.
- Sections: Top songs (5 rows + "Show all") · Albums · Singles & EPs · Videos · Similar artists (cookie portraits) —
  cards 38% width clamped 120-180px (Musify `artist_page.dart:226-262`). Optional pinned tab bar Top songs / Albums
  with a 24px-radius tinted indicator (BloomeeTunes `artist_view.dart:543-676`).
- Top bar fills in after the hero scrolls past.

### 10.4 Charts page (P1, M) — BloomeeTunes. Needs: charts endpoint (YT Music `FEmusic_charts`, PRODUCTION_PLAN P2)
- #1 hero: blurred cover background, huge "1" watermark (260px, weight 900, white .06), "#1 ON CHART" eyebrow
  (11/800, +2px tracking, accent), stat chips Peak / Weeks / Change.
- Rows: rank 24/700 tabular, gold `#CD9A7B` top 3, silver `#B3B9C5` top 10, `#555` after; trend ↑n `#5EFF43`,
  ↓n accent, rotated "NEW" `#18C9ED`; 54px art (`chart_view.dart:493-762`, `chart_list_tile.dart:50-238`).
- Country picker chip in the header (needs region setting).

---

## 11. New screens

### 11.1 Settings (P0, M) — Gyawun + Flow + BloomeeTunes. Needs: `/api/settings` (json) for server-side keys
- Search field on top (indexes every setting), then grouped cards (radius 20, white .03) with coloured 34px square
  icons and a subtitle per row; toggles, radios, chips; danger rows in red.
- Sections:
  - **Appearance**: theme (Cover colours / Accent / Pure black), accent picker, player background (with preview
    sheet), seek bar style (Classic / Squiggly), ring style, lyrics style (size, blur on/off, word fill), home
    sections order.
  - **Player**: autoplay/radio, swipe actions, double-tap seek, skip silence segments (SponsorBlock at play time),
    crossfade (later), sleep timer default.
  - **Sources & quality**: default source, Monochrome on/off, preferred codec, cellular vs Wi-Fi quality.
  - **Library & Keep**: download folder, Opus/FLAC, lyrics sidecar, ReplayGain, auto-keep liked.
  - **Services**: Navidrome status (moves here from the header dot), ListenBrainz / Last.fm tokens, yt-dlp version +
    Update, Reset YouTube session, Diagnostics (copy logs).
  - **Data**: backup / restore, clear search history / recently played / stats.
  - **About**: version, update banner, licences.

### 11.2 Stats (P1, M) — ArchiveTune. Needs: play log + `/api/stats?period=`
Period chips (Week / Month / Year / All); total listening time card (36px/800 accent number); donut of top artists;
favourite artist + favourite song cards; listening by day (bars) and by hour (24h clock); top 5 lists with ranks
(ArchiveTune `ui/screens/StatsScreen.kt`, Musify `time_machine_page.dart:231-314`).

### 11.3 Recap stories (P1, L) — Flow + Musify Time Machine + ArchiveTune wrapped. Needs: `/api/recap?period=`
Full-screen story pages, 6.5s each, 4px segment bars with 4px gaps; tap left 30% = back, elsewhere = next, hold =
pause. Pages: Intro · Minutes listened (count-up 1500ms) · Top artist + share % · Top songs · On repeat · 24-hour
listening clock · Longest streak · New to you · Most skipped · Summary card. Elements fade/slide in over 1000ms with
200ms stagger; blob shapes morph in the background at 10-16% opacity. "Share" renders the summary to a canvas PNG →
`navigator.share({files})` (needs HTTPS) (Flow `recap/story/*`, `StoryAutoplay.kt:25`; ArchiveTune/Metrolist
`wrapped/*`).

### 11.4 Bedside mode (P2, M) — ArchiveTune `AodPlayerScreen.kt`
Black screen with a big clock, art and title; dims after 5s; position drifts a few px every minute (burn-in); slide
to exit. Wake Lock API (needs HTTPS).

### 11.5 Onboarding (P2, S)
First run: pick region/language (charts and moods), pick a few artists (seeds Home), choose theme. Needs: region in
settings.

### 11.6 Song recognition (P2, L)
200px cookie-shaped mic button pulsing while listening; result card with Play / Search / Try again (Flow
`RecognitionScreen.kt:82-250`). Needs HTTPS for `getUserMedia` and a server step (e.g. `shazamio`); legally grey,
optional.

### 11.7 Your taste (P2, M)
Pentagon radar of genres/moods from the play log, under Stats (Flow ss13).

---

## 12. Backend additions the UI needs

| Endpoint / store | Used by | Notes |
|---|---|---|
| `POST /api/listens`, `listens.json` | Speed dial, personal shelves, History, Most played, Stats, Recap | count at 30s or 50%; store skips and play duration; also feeds scrobbling (PRODUCTION_PLAN P1.4) |
| `GET /api/stats?period=`, `GET /api/recap?period=` | §11.2, §11.3 | aggregates over listens |
| `/api/likes` | heart everywhere, Liked tile | json set |
| `/api/playlists` (+ `.m3u` export) | Library, add-to-playlist | CRUD, pin, folder, order |
| `/api/settings` | Settings | server-side keys only; UI-only prefs stay in `localStorage` |
| stream info: `codec`, `bitrate`, `sample_rate` | quality badge, song info | resolver already knows them |
| lyrics offset per track | lyrics nudge | stored with the lyrics cache |
| charts + region | charts page, onboarding | YT Music `FEmusic_charts` |
| `/api/art/<id>?size=64` | blur backgrounds, palette | already sized; make sure tiny sizes are cheap |
| follow list | artist Follow, "New from artists you follow" | later |

---

## 13. Build order

1. **Foundation** (S-M): tokens (§1), cover palette, joined-group and press styles, skeletons, HTTPS via Tailscale.
2. **Player core** (M-L): mini player (§3.1-3.2), Now Playing layout, backgrounds, seek bar, stretchy transport,
   action row, art swipe (§4), queue peek + pull-up and queue rows (§4.7, §7).
3. **Lyrics** (M): depth, line bounce, interlude dots, resync (§5); word fill after.
4. **Sheets** (M): track menu, row swipes, sleep timer, song info (§8).
5. **Discovery** (M): search (§10.1), album/playlist (§10.2), artist (§10.3), Home order + Quick picks + shelves
   (§9 items 1-2, 4, 7).
6. **Library + data** (M-L): `/api/listens`, likes, playlists; Library hub (§6); speed dial and personal shelves.
7. **New screens** (M-L): Settings (§11.1), Stats, Recap stories, charts page.
8. **Polish** (M-L): mini→full morph (§3.3), wide layout (§2.2), bedside mode, share cards, onboarding.

Test each step on an iPhone (Safari tab and Home Screen app): gestures vs the back-swipe, blur performance on long
lists, locked-screen playback, safe areas, and the Playwright phone-viewport script against the phone.

## Not adopted

- Equaliser, volume slider, real-time visualiser, haptics: iOS web limits (§0).
- Listen Together rooms, Discord presence, Android Auto, home-screen widgets: no fit for a single-user PWA.
- Bundled fonts or assets from any of the apps (Gilroy, Paytone, icons, logos).
