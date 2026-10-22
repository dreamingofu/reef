# Reef — handoff notes (resume on another machine)

Last updated: 2026-10-22 (Round 16, UI remake step 5, audit batches 1-7 accepted by the root and committed locally;
batch 3 = credentials / API / CSRF, batch 4 = updater health auth, batch 5 = JS proof in CI, batch 6 = frontend
async correctness, batch 7 = tiny-art cache byte budget). The required audit / refactor work is complete; `9d71366` was
later pushed and installed on the Android phone with the owner's authorization. Open: an unreproduced owner report
(Home → Library stops playback), see CURRENT.

## CURRENT supervision (read first; 2026-10-22, written for a session rotation)

- **Owner's goal**: finish the current task, then the necessary whole-codebase refactors, implemented by Claude and
  verified and accepted independently by Codex (root).
  - Historical (batches B1-B7): root acceptance allowed a local commit only; no push, deploy or U6.
  - **Current authorization (supersedes the line above)**: after B7 the owner authorized pushing the feature branch
    and installing it on the Android phone. Feature SHA `9d71366c46030cd4c70e96cdc47f981de018fc65` is pushed
    (`origin/dreamingofu/codebase-overview-and-risk` = `9d71366`) and installed on the phone, with config and data
    preserved. `origin/main` is unchanged at `f8f09cd`. Root's live GET of the phone's `/api/version` confirmed
    `self_update.allowed=true`, Reef `9d71366`, latest_reef = main `f8f09cd`, behind false. No new push or deployment for later tasks until the root
    accepts them. U6, merging to main and other publication still need the owner.
  - No secrets, permissions, defaults or model changes.
  - **Status**: B1-B7 are complete (accepted, committed, pushed with `9d71366`). One owner report is open (iPhone
    Home Screen app: the player shows paused after tapping Library). An uncommitted candidate patch awaits root
    acceptance; its cause is unconfirmed and it is unproven on a physical iPhone: see "Open playback report" below.
- **Commits on `dreamingofu/codebase-overview-and-risk`** (all pushed with `9d71366`):
  - U5 `bd5014d`.
  - Batch 1 `8fc8de93c619e1e674ade79fd5e2fe8eead4a698`.
  - Batch 2 `2ba24bbd0434e2b932cd44e7baf9c62628118d23` (accepted clean base of batch 3).
  - Batch 3 `4fbc2ea09c98d98602573910e0f8d3f7d116365d` (accepted clean base of batch 4).
  - Batch 4 `e920c5714c7bc19e8c484e5cc0e29d6e04feaec4` (accepted clean base of batch 5).
  - Batch 5 `afbcffeb5c535a59669784612193a25a51a5b2f8` (accepted clean base of batch 6).
  - Batch 6 `4f87f8bc7dc2108495820d7b57a8abe5055c9417` (accepted clean base of batch 7).
  - Batch 7 `3fb3a90519a1969eef9debd869013bbcffa82d34` (art cache byte budget, parent `4f87f8b`; `app.py` +
    `tests/test_artcache.py` only).
  - Final documentation milestone: the local commit directly on top of `3fb3a90` that adds this text (`HANDOFF.md`
    only; `git log -1`; its hash is in the milestone's worker_done report to the root).
- **Who's working**:
  - Root coordinator: `term_d76429bc-6214-450e-8f42-08b9079686d6`, sole supervisor and sole review / acceptance owner of
    Run `run_644fd9908c5b` generation 4 (replaced generation 3's `term_3c425302-cae4-4be9-97c0-cb65b1ba1535`).
  - Claude writer (batch 7): `term_a04672b3-ae4f-4038-a702-8e1fffe71717`, sole writer of Task `task_e8438201fabe` /
    Dispatch `ctx_ff17d4d9ba8a` (art cache byte budget, base `4f87f8bc7dc2108495820d7b57a8abe5055c9417`; settled),
    then the final milestone Task `task_aa16cca11953` / Dispatch `ctx_6d49e79e35f1` (the batch 7 commit of `app.py`
    and `tests/test_artcache.py`, then this file's final commit, nothing else). After that commit this Claude
    **positively relinquishes checkout writer ownership** and idles (tab kept, shell open). No writer work follows.
  - Previous Claude writer (batch 6): `term_0f126efc-cb50-4555-a543-c524e4c799da`, relinquished writer ownership after
    the batch 6 commit and idles (tab kept). It was the sole writer of Task `task_fd512d52fe1c` /
    Dispatch `ctx_90a49cc91a95` (frontend correctness, base `afbcffeb5c535a59669784612193a25a51a5b2f8`), then the
    root review repair Task `task_1af4fce8fc0b` / Dispatch `ctx_f45a568106c1` and the search follow-up Task
    `task_6754636a7a42` / Dispatch `ctx_a9fbd9e30396` (all settled), then this milestone Task `task_3dcc97804933` /
    Dispatch `ctx_caf9d7eb927e` (this file and the local batch 6 commit of `static/app.js`, `tests/js/harness.js`,
    `tests/js/app-async.test.js`, `HANDOFF.md`, nothing else). After that commit this Claude **relinquishes
    checkout writer ownership** and idles (tab kept, shell open). A fresh Claude owns the next batch only after the
    root confirms its readiness and sends a new Dispatch. No duplicate writers.
  - Batches 1-7 are closed (accepted, committed); their proof stands and isn't re-reviewed or re-run.
  - Previous Claude writer `term_52e489b5-69d0-4ab7-bf9e-78308462b9b3` relinquished writer ownership after the batch 5
    commit and idles (tab kept).
    - Settled batch 5 Task `task_c3403283cf1f` / Dispatch `ctx_5b4db1c2fe75` (JS proof in CI, on base
      `e920c5714c7bc19e8c484e5cc0e29d6e04feaec4`).
    - This milestone Task `task_fb5a4400ad3c` / Dispatch `ctx_ba1c6274faa3` owns only this file and the local batch 5
      commit (`.github/workflows/ci.yml`, `tests/js/**`, `HANDOFF.md`), no other work. After that commit this Claude
      **relinquishes checkout writer ownership** and idles (tab kept, shell open). A fresh Claude owns the next
      frontend batch only after the root confirms its readiness and sends a new Dispatch. No duplicate writers.
  - Previous Claude writer `term_8969653d-0e79-47c1-8e88-3ce3b86a64c6` relinquished writer ownership and idles (tab kept).
    - Settled batch 3 Tasks: `task_f48c451fcebe` / `ctx_5ea180eba2eb` (implementation) and `task_4434847b17e2` /
      `ctx_8c5e9a8b2d1c` (long-userinfo repair).
    - Settled milestone Task `task_e17f105b79e5` / `ctx_65bb1c6ba326` (this file + the local batch 3 commit
      `4fbc2ea09c98d98602573910e0f8d3f7d116365d`).
    - Settled batch 4 Task `task_0a8f23021e6d` / `ctx_3e5028b3c2af` (updater health auth, F8, on base `4fbc2ea`).
    - Settled milestone Task `task_6227f44ba099` / `ctx_5c4d5f9ab3e7` (this file + the local batch 4 commit
      `e920c5714c7bc19e8c484e5cc0e29d6e04feaec4`), after which it relinquished writer ownership.
  - Old Claude `term_62492f92-3dea-4cc5-ac14-f76f76a4bdb7` relinquished writer ownership and stays idle (settled Tasks
    `task_a93a61d0b573` batch 1, `task_e630e35fb597` / `task_dd2cff493c45` batch 2, `task_3035a994e8c6` milestone).
  - All prior Claude writers stay idle with their tabs kept. No browser or server is running (the root closed its
    scratch WebKit browsers and QA servers).
- **Audit**: `/tmp/reef-codebase-audit-20261022.md`, report plus a superseding ADDENDUM. Don't redo the survey.
  - Root-confirmed findings:
    - F1 / F5 / F6 / F7 / F11 (synthetic repros);
    - F3, generic keep collision, and F9, .m3u export collision (both fixed in batch 2);
    - F4: `errors.payload` and `navidrome.ping` leak the raw request URL (Subsonic token / salt, query secrets).
  - The root has now read the relevant sheets / settings parts of app.js (a bounded review). There is no claim of
    perfect correctness.
  - Device, Safari, performance and real-service gaps remain.
- **Batch 1 (persistence: F1, F5, F6 store half)**: accepted, committed `8fc8de9`.
  - Root checks: an independent diff review; 155 focused tests passed; full pytest 1003 passed / 1 deselected; Ruff
    and diff check clean.
  - A root scratch probe showed that a like added concurrently after the preflight and the snapshot is kept.
- **Batch 2 (keep / .m3u / .lrc collisions: F3, F9 + root repairs)**: **accepted by the root** after the repairs,
  committed locally. Root checks:
  - an independent final diff review;
  - `.venv/bin/python -m pytest -q tests/test_downloader.py tests/test_lossless.py tests/test_library.py
    tests/test_lyrics.py` → 150 passed;
  - full suite 1020 passed / 1 deselected; Ruff and diff check clean;
  - `PYTHONPATH=. .venv/bin/python /tmp/reef-codex-refactor-20261022/ytdlp-intermediate.py` (real pipeline: the
    correct 1 s audio, the 4 s original kept);
  - `/tmp/reef-codex-refactor-20261022/file-ownership.py` (alias ownership, the export `.tmp` link target, the lyrics
    sidecar link target).
- **Batch 3 (credentials / API / CSRF: F2, F4, F6 app half, F7, F11)**: **accepted by the root** after its independent
  final diff review (no blockers), committed locally. Changed: `log.py`, `errors.py`, `navidrome.py`, `app.py`, `tests/test_errors.py`, `tests/test_service.py`,
  new `tests/test_request_guard.py`, and `sources/failover.py` only for its log line (`why` redacted before the
  80-char cut, per root review); raw cooling keys / identity unchanged, masking is at presentation (log formatter,
  `redact_obj` keys, health / diagnostics).
  - `log.redact`: URL userinfo (to its last `@`), `api_key/key/token/access_token/password/auth` (whole names, any
    case) on top of the googlevideo / Subsonic params; idempotent (an existing mask isn't re-masked).
    `redact_obj` masks dict keys. `errors.message(e, limit)` = redact then cut; `errors.payload` classifies the raw
    text, then uses it. `navidrome.ping`, `/api/health`, `/api/health/sources`, the playlist export 500 and every
    `str(e)` presentation / log line in app.py go through it.
  - `refuse_cross_site` before_request (after the password check): non-GET/HEAD/OPTIONS refused (403) when
    Sec-Fetch-Site isn't same-origin/none, or Origin is present and isn't exactly this request's (scheme, host,
    port): `null`, malformed, userinfo / path, other port or scheme all refuse. Own scheme = socket scheme, or
    X-Forwarded-Proto only from a loopback peer (Tailscale Serve); X-Forwarded-Host / -For never read; auth policy
    unchanged. No Origin and no fetch metadata (curl, scripts, update.sh, tests) pass.
  - `_json_object()` for the `get_json(silent=True) or {}` routes (blocklist, prefetch, download, download/batch,
    playlists create / tracks / move, match fix); likes / dislikes / update keep their own 400 for non-objects;
    sendBeacon force-parse, multipart restore / import and optional bodies unchanged. `_seconds` catches
    `OverflowError`.
  - Checks by Claude: 71 new regressions failed on a `git archive` export of `2ba24bb` and pass now; full suite 1130
    passed / 1 deselected; Ruff and diff check clean; root matrix `guard-root3.py` 19 cases, 0 failures.
  - Follow-up repair Task `task_4434847b17e2` / `ctx_8c5e9a8b2d1c` (root-reproduced long-userinfo leaks): `settings._url`
    masks the URL before its 60-char cut (validation and stored raw URLs unchanged); `health._row` and the crashed-check
    text mask before their 200 / 100-char cuts. 4 new regressions (tests/test_settings.py, tests/test_health.py, incl.
    `/api/health/sources` and the PATCH 400) failed before and pass now, plus a stored-raw credentialed URL check.
  - Root acceptance checks:
    - focused `tests/test_request_guard.py`, `test_errors`, `test_service`, `test_health`, `test_settings`,
      `test_failover`, `test_backup`, `test_listens`, `test_import`, `test_library`, `test_lyrics`, `test_personal`,
      `test_matching` → 484 passed;
    - full `.venv/bin/python -m pytest -q` → 1135 passed / 1 deselected; `.venv/bin/ruff check .` and
      `git diff --check` clean;
    - `/tmp/reef-codex-refactor-20261022/guard-root3.py` → 19 cases, 0 failures;
    - `credentials-root3.py` → seven presentations, 0 leaks, raw failover identity preserved, plus a real requests
      error against a closed loopback port;
    - isolated WebKit `api-browser-root3.js`: like 201, text/plain listen 201, personal 200, multipart restore 200,
      import 201, no-body like 201, malformed array download 400, real cross-origin restore POST 403 (screenshot
      `api-browser-root3.png`).
  - Not proven: on-device Safari, real services, an actual Tailscale Serve deployment.
- **Batch 4 (updater health with auth, F8)**: **accepted by the root** after its independent final diff review (no
  remaining blockers), committed locally. Changed:
  `updater.py`, `termux/update.sh`, `tests/test_updater.py`, `tests/test_update_sh.py`, new `tests/fake_reef.py` (a
  loopback fake Reef: /api/health + /api/diagnostics behind werkzeug's Basic parsing, optional 302).
  - `updater._auth_headers()`: Basic `reef:<password>` only when `config.OPEN` and `config.PASSWORD` (config's own
    env-then-reef.env precedence). `_healthy` probes through an opener with no proxy and no redirects (a 3xx =
    unhealthy, the other origin is never asked); version check, wait bound and rollback unchanged.
  - `update.sh`: `env_file_value` reads reef.env as data like `config._load_env_file` (never sourced; first line of a
    key wins, whitespace then quotes stripped; `export X=` isn't key X). Environment wins (`${REEF_OPEN-…}`,
    `${REEF_PASSWORD-…}`: set-but-empty wins too; port keeps `${REEF_PORT:-…}`, now first-line-wins and digits-only,
    else 5274). With OPEN=1 + a password the curl checks send `Authorization: Basic` via `-H @-` on stdin (not
    argv / output); all probes `--noproxy '*'`, no `-L`. `REEF_UPDATE_HEALTH_CMD`, commit check, rollback unchanged.
  - Regressions (password with spaces, `ü`, `$(touch PWNED)`, backticks, `;|&'"\` and `:`): 7 fail on a `git
    archive` of `4fbc2ea` (Python auth, header accepted by the real app, proxy/redirect; shell env auth, rollback
    health under auth, reef.env parsing, env-over-file) and 2 more fail on a copy without the proxy / redirect
    hardening; all pass now. Preserved: no-auth default (no header sent, also with a password but OPEN=0), wrong /
    missing password fails, wrong version / commit stays unhealthy, existing fake-origin / fake-pip / fake-boot tests.
    No PWNED file, password or token in output.
  - The root caught the first patch's redirect hole: an off-origin 302 got the Basic header and was taken as
    healthy. The final no-proxy / no-redirect policy fixes it, with a committed regression.
  - Root acceptance checks:
    - `.venv/bin/python -m pytest -q tests/test_updater.py tests/test_update_sh.py` → 41 passed;
    - full `.venv/bin/python -m pytest -q` → 1150 passed / 1 deselected (47.5 s: the shell rollback waits);
    - `bash -n termux/update.sh`, `.venv/bin/ruff check .`, `git diff --check` clean;
    - `/tmp/reef-codex-refactor-20261022/updater-auth-root3.py`: the actual Reef app with OPEN=True over scratch HTTP;
      Python and shell health: right auth True, wrong auth False, wrong version False, wrong commit False, redirect
      False, the redirect target got 0 requests. All scratch servers closed; no real update, service or phone.
- **Batch 5 (JS proof in CI)**: **accepted by the root** after its independent final review of the JS harness / tests
  and the CI diff (no blockers), committed locally. Implementation Task `task_c3403283cf1f` / `ctx_5b4db1c2fe75`.
  Changed:
  `.github/workflows/ci.yml` (new `js` job, Node 26 via `actions/setup-node@v4`: `node --check static/app.js`,
  `node --test tests/js/`; the Python job is unchanged), new `tests/js/`. No product source changed.
  - `tests/js/harness.js`: `load(name)` slices the text between `// ==== <name>:start ====` / `:end ====` (whole
    lines, exactly one of each, start first, else a clear error naming the marker and counts), checks that the slice
    starts with `const <ID> = `, and compiles it with `node:vm` (`runInThisContext`, strict mode, `lineOffset` so stack
    traces name static/app.js lines). It runs in this realm so `deepStrictEqual` works on its results. `snapshot()` =
    structuredClone check that inputs weren't mutated.
  - `tests/js/index.js`: newer Node resolves `node --test tests/js/` to this file (without it: MODULE_NOT_FOUND on
    Node 26); it requires every `*.test.js`. `node --test tests/js/*.test.js` gives the same 186.
  - One `*.test.js` per core (Q, R, L, M, LY, LV, P, PC, SH, DC) plus `harness.test.js` (app.js declares exactly these
    ten cores; missing / duplicate / reversed / inline markers, wrong declaration, strict mode and line numbers).
    Ported from the scratchpad u1-u5 / b2 / b5 / b6 / p0c / p4d checks (they passed on `e920c57` first), with added
    cases: exports and constants at their boundaries, inputs never mutated, results never aliasing inputs, seeded and
    mocked `Math.random` (`t.mock.method`), mocked `Date` (`t.mock.timers`), exhaustive `moveTrack` / `reinsert`
    round-trips, binary search versus a linear scan, 60 seeded random covers through `P.derive` (roles valid, base
    dark, accent and on-accent >= 4.5:1, deterministic). No production logic copied.
  - Checks by Claude: `node --check static/app.js` exit 0; `node --test tests/js/` 186 passed / 0 failed (an
    injected failing canary made it exit 1, then removed); `.venv/bin/python -m pytest -q` 1150 passed / 1 deselected;
    `.venv/bin/ruff check .` and `git diff --check` clean. Two assertions failed while writing; both were the tests'
    own wrong expectations (`L.counts(99.9, 200)` is true by the 30 s rule; library-core has 4 functions), fixed in
    the tests. No product failure found.
  - Root acceptance checks (root generation 4):
    - `node --check static/app.js` and `node --test --test-reporter=dot tests/js/` → 186 passed, exit 0;
    - `.venv/bin/ruff check .` and `git diff --check` clean;
    - Claude's full pytest (1150 passed / 1 deselected) reused: no Python product change;
    - all ten cores are the actual source; no dependencies, bundler, modules or product split; `static/app.js` and
      `app.py` unchanged.
  - Limits: only Node 26 was run locally (CI pins 26). Tests run in Node's realm, not a browser; a core that silently
    used a Node global of the same name as a browser one wouldn't be caught. Not proven: Safari / WebKit, the DOM
    code around the cores.
- **Batch 6 (frontend async correctness: U-a, pinFix races, restore refresh)**: **accepted by the root** (generation
  4) after its independent review of the final diff, committed locally on `afbcffe`. The root's review found two
  defects in the first patch, both repaired with regressions that failed first (details below): an old PUT finishing
  after the same song's sheet reopened still swapped the queue (hidden because the tests stubbed `swapEverywhere`),
  and pre-restore catalog page / search / chip-list / refetch answers re-applied stale liked / kept flags through
  `noteKept`. Changed:
  `static/app.js`, `tests/js/harness.js` (new `decl` / `scope`), new `tests/js/app-async.test.js`. No server change.
  - `api()`: valid 2xx JSON unchanged; 204 is null; a 2xx body that isn't JSON (HTML, empty, cut) rejects with
    "Reef's reply wasn't JSON" (`status`, `body: {}`, `cause`), never `{}`. Non-2xx: `body.error`, else statusText,
    else `Request failed (<status>)`; a JSON body that isn't an object (`null`, array) or no JSON becomes `body: {}`
    (no more `Cannot read properties of null`); typed `status` / `body` (block, hint, job) kept.
  - Fix wrong match: `fix.session` counts openings (`openFix` bumps it, even for the same placeholder object, and
    frees `busy` and the sheet's busy class). `pinFix` captures `t` and the session, claims `busy` **before** waiting
    on the pending key (a second pick meanwhile is dropped: one PUT), drops the pick if the session changed while
    waiting (never a PUT under a newly opened song's key), reads the key once and uses it for both the PUT and
    `applyFix` (not the mutable `fix.key`), and frees busy only for its own session. An old PUT finishing after a
    reopen (success or failure) changes nothing in the UI: no queue swap, no reload, no close, busy kept (the server
    pin happened; the newer sheet's pick decides the queue). `applyFix` keeps its base signature. Normal pick, API
    failure + retry, pending-key failure + later search unchanged.
  - **Root review repair** (`task_1af4fce8fc0b`): the first patch still applied a stale-session PUT with
    `close: false`. Root repro `/tmp/reef-codex-refactor-20261022/pin-reopen-put-root4.cjs` (actual fix / openFix /
    runFixSearch / pinFix / applyFix / swapEverywhere): the same song reopened, a second pick while the first PUT
    waits, old answers first → the old swap replaced the queue id, so the latest pin found nothing to swap
    (`finalQueue: [playing, pick-one]`, exit 1). The fix tests had stubbed `swapEverywhere`, which hid it; they now run
    the real `swapEverywhere` (M.swap) and assert `state.queue`. Root also found that a pre-restore catalog page
    (album / artist / playlist) answering after the restored `loadLikes` re-applied old `liked` flags through
    `noteKept`: `loadPage` now captures `restoreGen` and calls `noteKept` only if no restore happened since (the page
    data is still cached; no catalog reload).
  - **Search follow-up** (`task_6754636a7a42`): root repro `/tmp/reef-codex-refactor-20261022/restore-search-root4.cjs`
    (actual `loadKind` + `noteKept`): a `songs` chip list asked before a restore and answering after it wiped the
    restored like (`likes: []`, exit 1). The same read path was in `runSearch` and `refetchSearch`. Each now captures
    `restoreGen` with its search seq and calls `noteKept` only if no restore happened since. Results, chip lists,
    rendering and the `searchSeq` / query checks are unchanged, and a search asked after the restore still updates
    likes / kept. Mutation replies (`resolvePlaceholder`, dead-track replacement, `applyFix`) keep their behavior.
  - Restore success: `restoreGen++` and `blockGen++`, drops `local/*` (Reef playlist) entries from `detail.cache` /
    `errors` / `inflight` (other pages kept), clears `lyr.cache` and resets the lyrics view if it holds a track
    (`resetLyrics`, reloads when shown), then reloads settings, history, blocklist, Home **plus likes and playlists**,
    and the shown Reef playlist page (`loadPage(force)`). Playback (queue, index, audio src / position / play state)
    isn't touched. `loadLikes` / `loadPlaylists` / `loadSettings` / `loadHistory` / `loadHome` drop an answer (success
    or error) asked before the restore (`DC.genCurrent(gen, restoreGen)`); `loadBlocklist` already used `blockGen`.
    `loadPage` ignores an answer whose promise is no longer the key's inflight one (cache, errors and the newer
    inflight entry untouched; awaiters get `loadPage(key)`, i.e. the current load), and skips `noteKept` for any page
    asked before a restore. `fetchLyrics` deletes a failed
    entry only if it is still that read's promise.
  - Harness: `scope(names, vars)` compiles the named top-level declarations of static/app.js (functions, consts,
    lets; `decl` finds each exactly once) in one strict scope at their real line numbers over stub vars. Tests run the
    shipped `api`, `openFix` / `runFixSearch` / `pinFix` / `applyFix` / `fixQuery` / `fix`, and `restoreBackup` with
    `loadPage`, the six loaders, `fetchLyrics` / `showLyrics` / `resetLyrics`, `noteKept`, `detail`, `PAGE_API`,
    `likedIds` / `keptIds` / `ndIds`, plus the real `swapEverywhere`, with
    deferred API replies, real `Response` objects and real M / SH / DC cores. Each test has a 5 s timeout.
  - Before / after: the 20 new tests on a `git archive` of `afbcffe` → 12 fail by assertion, 8 pass (preserved
    behavior), 0 cancelled (`/tmp/reef-batch6/base-app-async.txt`); after → 20 pass. A first draft hung on base (an
    unanswered stale PUT awaited); fixed by asserting PUTs after a flush before awaiting. Mutation probes on a scratch
    copy: removing any single guard (`restoreGen++`, the restore's `blockGen++`, the lyrics identity delete, the
    loadPage inflight check, the openFix busy reset, the session check after the key wait) fails at least one test.
  - Repair before / after: the 2 new tests (same song reopened with the old PUT in flight: queued and current-track
    success plus an old failure; pre-restore album page vs restored likes) on the first patch → both fail by assertion,
    0 cancelled (`/tmp/reef-batch6/repair-before.txt`: old answer swapped the queue to `pick-one`; `liked-old` came
    back; the current-track case alone also fails, `repair-before-current.txt`); after → 21 / 21 pass and the root
    repro prints `finalQueue: [playing, pick-two]`, exit 0. Mutation probes: dropping `&& live()` before `applyFix` or
    the `restoreGen` check before `noteKept` each fails one test.
  - Search before / after: 3 new tests (`runSearch`, `loadKind`, `refetchSearch` with the real `noteKept`, replies
    crossing a restore, then a post-restore read that must still update flags) fail by assertion on the prior repair,
    0 cancelled (`/tmp/reef-batch6/search-before.txt`); after → 24 / 24 in `app-async.test.js`, and the root repro
    prints `likes: [restored-song]`, `kindStored: true`, exit 0. Removing any one of the three guards fails exactly
    its own test.
  - Checks by Claude: `node --check static/app.js` ok; `node --test tests/js/` → 210 passed (186 + 24), 0 failed;
    `.venv/bin/ruff check .` and `git diff --check` clean. Python unchanged: batch 4's full pytest (1150 / 1) stands.
  - Root acceptance checks (root generation 4):
    - `node --check static/app.js`; `node --test --test-reporter=dot tests/js/` → 210 passed; Ruff and diff check
      clean; Python unchanged (batch 4's full pytest 1150 passed / 1 deselected reused);
    - actual shipped repros: `pin-reopen-put-root4.cjs` → the latest pick (`pick-two`) survives;
      `restore-page-root4.cjs` (with DC) → restored like kept, catalog page cached; `restore-search-root4.cjs` →
      restored like kept, search list stored; all exit 0;
    - WebKit `/tmp/reef-codex-refactor-20261022/frontend-browser-root4.js` (static-only loopback server, synthetic
      API and WAV, external requests blocked before navigation): pending-key target switch sends no stale PUT; two
      form submissions on a pending key send one PUT; a reopened same target's old PUT can't change queue / busy and
      the latest pick wins; a malformed 2xx JSON shows an error; restore refreshes likes / playlists / the cached
      local playlist and lyrics, and an old search can't undo a restored like. Audio kept the same src, time 13.361 →
      15.493 s, `paused` false before and after, no pause / emptied / loadstart events, no page errors. Proof:
      `frontend-browser-root4-result.json`, `frontend-browser-root4.png`. All scratch browsers and the QA server closed;
      no real services, data or phone.
  - Limits: Node only for the writer's own checks (WebKit by the root). A stale playlist-page load with no newer load re-asks the page
    once (`loadPage(key)`) even if nobody shows it. Stale non-restore overlaps of the same loader (two loads without a
    restore) keep their old last-answer-wins behavior. An old pick's error toast still shows after a reopen; an old
    pick's success is silent. Restored `matches` don't re-resolve placeholders already in the queue. Every read that calls
    `noteKept` (Home, pages, search, chip lists, refetch) is now restore-gated; mutation replies aren't (by design).
- **Batch 7 (U-b: tiny-art cache byte budget)**: **accepted by the root** (generation 4) after its independent review
  (no blockers), committed locally as `3fb3a90519a1969eef9debd869013bbcffa82d34` on `4f87f8b`. Changed: `app.py`
  (the tiny-art constants and the insert in `_tiny_bytes` only), new `tests/test_artcache.py`.
  - `ART_TINY_BUDGET = 16 MiB` beside the unchanged `ART_TINY_KEEP = 400`. On insert, under `_tiny_lock`: the entry is
    set (a same-url fill replaces, never adds) and moved to most recent; the retained payload total is summed from
    the entries (at most 401, so no persistent counter to drift after the tests' `_tiny_art.clear()`); least recent
    entries are dropped while count > 400 or bytes > budget. Hits still refresh LRU order. The fetch stays outside
    the lock. The fetched (bytes, mime) is always returned, even when it isn't kept (only possible if one image were
    bigger than the budget). Per-response 512 KiB cap, status / mime checks, redirects / `/api/thumb` no-redirect and
    off-origin behavior unchanged; failures still cache nothing.
  - `tests/test_artcache.py` (9 tests): real defaults with 33 × 512 KiB (all returned, ≤ 32 kept, ≤ 16,777,216 bytes,
    the oldest evicted); byte vs count boundary with hits refreshing LRU (KEEP 4, budget 10 bytes); one image over
    the budget is served but not kept; 404 / text/html / oversize / OSError leave a filled cache unchanged (4 cases);
    concurrent fills (4 fills of one URL with different lengths + 4 new URLs, all released together by a
    `threading.Barrier(timeout=5)` inside the fetch, joins bounded at 10 s) with real defaults over a 15.5 MiB prefill,
    and with KEEP 3 / 40 bytes: each caller gets the bytes its own fetch read, both limits hold, a retained same-url
    entry is one of the fetched payloads, and a later fill still keeps both limits.
  - Before (accepted base, 3 runs, identical): 5 failed / 4 passed (failure cases are preserved behavior).
    Assertions: `33 <= 32` (count of 33 × 512 KiB); `['b', 'c', 'a', 'd'] == ['c', 'a', 'd']` (no byte eviction);
    `4 <= 3` (over-budget image kept); `17188864 <= 16777216` (concurrent, defaults); `52 <= 40` (concurrent, small).
  - After, checks by Claude: `.venv/bin/python -m pytest -q tests/test_artcache.py tests/test_ui_foundation.py
    tests/test_discovery_ui.py` → 72 passed (existing art / thumb proxy, redirect and bounded-memory tests included);
    the two concurrent cases re-run 20 times, all passed; `.venv/bin/ruff check .` and `git diff --check` clean. Full
    pytest and node not re-run by Claude (the root runs them at acceptance).
  - Root acceptance checks (root generation 4, on the exact working code):
    - `.venv/bin/python -m pytest -q` → 1159 passed / 1 deselected (47.97 s); `node --test --test-reporter=dot
      tests/js/` → 210 passed; `node --check static/app.js`, `.venv/bin/ruff check .`, `git diff --check` pass;
    - accepted blobs: `app.py` `0f1f836839c9b33e255bb4da4a8ff65dc0dacf00`, `tests/test_artcache.py`
      `5efe572c32bf7b8a219ae90d9bde5794928820c7` (the committed blobs);
    - independent `/tmp/reef-codex-refactor-20261022/art-budget-root4.py`: 33 allowed 512 KiB responses → 32 entries
      / 16,777,216 payload bytes; 72 more concurrent fills → max observed 16,777,216, final 32 entries with the same
      bytes, every served bytes / mime correct; no external network, stores, services or workers;
    - Claude's 72 focused checks reused; batch 6's WebKit evidence stands (no product JS change in batch 7).
- **Completion / remaining gaps** (root generation 4, 2026-10-22):
  - All required, root-confirmed audit batches B1-B7 are accepted and committed locally; the scoped audit has no
    confirmed actionable defect left. The audit / refactor supervision is complete; no further writer work planned.
  - Deferred hypotheses (no work without new evidence): F10 (finished-job cap) stays inspection-only, with no shown
    user-facing failure or need; J1-J3 / U-c / U-d are unmeasured performance / gesture hypotheses and don't justify
    guessed refactors. No cosmetic whole-file splits.
  - Not planned at B7 (historical; the push / Android install of `9d71366` was authorized afterwards, see the top of
    this section): features, U6, push, deploy / publication, real data, devices / phone, real services, default /
    model / credential / permission / billing changes.
  - Not validated (no claim of a perfect codebase): live Safari / on-device behavior, real services, performance.
- **Open playback report (2026-10-22; open, cause unconfirmed)**: the owner reports that going from Home to Library
  stops the current playback. **Owner fact** (later): an iPhone **Home Screen app** (standalone), and the player
  shows **paused** after tapping Library.
  - Diagnosis Task `task_345d1961f336` / Dispatch `ctx_6feb00b1b864` (settled, outcome failed: not reproduced; read-only
    plus this file). Candidate Task `task_9e66d131e333` / Dispatch `ctx_be4699a4c48d` (below). Writer of both: Claude
    `term_d953e2c9-6cd5-4087-9a04-2690fdb9b176`; after its worker_done it relinquishes writer ownership and idles.
  - Not reproduced on `9d71366`, using a static-only loopback server (`templates/index.html` + `static/*`), mocked
    `/api/*`, a real WAV stream and external requests blocked:
    - Claude, WebKit, desktop clicks on `.tab[data-view]` (Library / Home × 4, empty fixtures): `paused` false at
      every step, same `#audio` element and `currentSrc`, time advancing (4 → 4.13 s), no pause / emptied / loadstart /
      error events, no page errors.
    - Root, the same with desktop clicks, and with iPhone UA + `isMobile` + `hasTouch` taps over populated history /
      playlists / recent albums: same result (`/tmp/reef-codex-refactor-20261022/nav-before-root4.txt`,
      `nav-touch-root4.txt`; script `nav-browser-root4.js`, server `ui-server-root4.py`).
  - Static read (no pause / src path found on that navigation): `route()` (`static/app.js` ~2653) only toggles
    views / tabs, calls `loadHome` / `loadLibrary` / `loadPlaylists` and `RENDER[view]`, and on a route change
    `closeNP()` (`closeQ` + classes only) and `closeSheet`. `renderLibrary` / `renderDownloads` / `renderLibPlaylists` /
    `renderLibNavidrome` / `renderKept` / `paint` / `markPlaying` / `updatePlayer` make no `audio` source / play-state
    writes (`updatePlayer` only reads it). The tab
    anchors match no `[data-…]` of the delegated document click handler (~6745). `#audio` sits outside every
    re-rendered container (`templates/index.html` 383). The only `audio.pause()` / `audio.src` sites: user pause /
    togglePlay, the mini swipe-down dismiss (`dismissMini`, `PC.miniVertical` dy ≥ 32 or a downward flick on
    `#mini-card`), the sleep timer, `stopAtEnd`, `loadPlaceholder`, recovery `moveOn` / `reload`, `load` /
    `playAudio`, and the Media Session pause action.
  - Platform lead (not proof for HTMLAudio): WebKit bug 215884 (comments 2 / 12): in iOS Home Screen apps (Web.app)
    a hash navigation behaves like "a navigation which stops all the capture tracks" (getUserMedia), fixed in the
    Safari standalone host, not WebKit; later comments report pushState / path changes doing the same in WKWebView. No
    source found yet for a fragment navigation pausing `<audio>`. "Paused" also fits a reload (the restored session
    shows a paused mini); the device can tell (does Library show at once with the same position, or reload?).
  - **Candidate patch** (root independently reviewed and accepted for a test deployment; physical symptom unconfirmed): `static/app.js`
    `tabClick(e)` (after `openRoute`), called first in the delegated document click handler. Only when
    `navigator.standalone === true`, for a plain primary click (no modifier, not `defaultPrevented`) on a bottom
    `.tab[href]` whose href is exactly `#home` / `#library` (no `target` / `download`): `preventDefault`, then
    `history.pushState(null, "", hash)` + `route()`; the tab already shown is a no-op (as the native same-hash link).
    `route` / `openRoute` / programmatic routes / legacy `#kept` / `#downloads` redirects / the Search FAB / See all /
    page links / `hashchange` traversal are unchanged. No auto-resume, no pause handling changed.
  - Tests (root review repair Task `task_12d4fa2a27f6` / Dispatch `ctx_1b99da06146e`; product code unchanged):
    new `tests/js/app-nav.test.js` (10) runs the shipped code through harness.scope over a history / location model
    (native fragment navigations and Back / Forward fire hashchange → `route`) and DOM stubs. Its two integration
    tests compile the **actual delegated document click listener** where it stands (its `document.addEventListener(
    "click", (e) => {` … `  });` lines become `const docClick = (e) => {` … `  };`; `tabClick` joins the scope only if
    declared), dispatch a tab tap, follow the link natively unless prevented, and model the suspected host pause (a
    native fragment navigation sets the player paused). The 8 unit cases cover depth / parent, Back / Forward with the
    saved scroll, Library → Home and a detail page's parent, same tab no entry, non-standalone / modified / middle /
    handled / target / download clicks, Search / See all / page links and other tab hashes staying native, and legacy
    `#kept` still redirecting.
    - Baseline (`git show 9d71366:static/app.js` in scratch, unchanged harness): the standalone integration test fails
      by behavior: `AssertionError: the tab's native fragment navigation is prevented` (`false !== true`); the
      non-standalone integration test and the legacy case pass (behavior kept); the 7 helper unit cases fail with
      `t.s.tabClick is not a function`. After: 10 / 10. Removing the listener's `if (tabClick(e)) return;` fails exactly
      the standalone integration test; each `tabClick` guard removed fails ≥ 1 unit case.
  - Checks by Claude: `node --check static/app.js`; `node --test tests/js/` → 220 passed (210 + 10); `.venv/bin/ruff
    check .` and `git diff --check` clean. Own WebKit (static-only scratch server, mocked API, WAV, external requests
    blocked, `navigator.standalone` forced true): Library / Home tab clicks ×4 → only `pushState` entries, no
    hashchange, `paused` false, same element / src, no pause / emptied / loadstart events, no page errors; one
    `history.back()` → hashchange → Library at depth 3. Root's WebKit with an explicit host-pause fault model
    (`pwa-before-root4.txt`: first Library tap paused, 1 native interruption; `pwa-after-root4.txt`: paused false, 0,
    same element / src, Back / Forward depths, same tab no extra entry, a manual pause stays paused). These prove the
    avoidance mechanism only, not the iOS cause.
  - Remaining device gap: physical iPhone Home Screen proof (tap Library while playing; also Back / swipe-back,
    which stays a native traversal, and the Search FAB / See all links, still native fragment navigations). If the
    pause persists with the patch, the hook is not the fragment navigation (or covers pushState too) and the patch
    should be dropped. Root acceptance covers this bounded candidate and regression proof, not a confirmed iOS fix.
  - Root final checks: `node --test tests/js/` → 220 passed (731.87 ms), JS syntax / Ruff / diff clean;
    `/tmp/reef-codex-refactor-20261022/pwa-root4-checks.json`. No Python changes; accepted B7 Python proof stands.
    Root's independent WebKit before/after fault-model evidence is above; no actual iOS standalone reproduction claimed.
    Claude positively relinquished ownership after `msg_ece47f534253` for the repair Dispatch and idles; all QA browsers
    and scratch servers are closed. Root owns acceptance, the local commit and authorized feature push / Android update.
- **Tooling note**: `worker-start --terminal` returns `agent_unconfigured`. The proven fallback is manual
  task-create, then `dispatch --return-preamble`, then terminal-send the exact preamble.

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

### UI remake step 1: Foundation (2026-10-22, uncommitted on `dreamingofu/codebase-overview-and-risk`)

- `static/style.css` starts with the design system: surface ramp, text levels, hairline, type scale, radii,
  `.joined` groups, motion tokens + `.press` / tab-in / `.list-in` / `.xfade` / `.sk` (250 ms delay, .25↔.5 pulse),
  the `.empty` / `.empty.err` state, `prefers-reduced-motion` (fades kept, movement dropped).
- Cover palette: `palette-core` in `static/app.js` (node check `scratchpad/u1/palette_core_check.js`), applied to
  `.cover-tint` (mini player, Now Playing) via `--c-base` / `--c-accent` / `--c-on-accent` (`@property`, 800 ms).
  Pref `reef.theme` = cover | accent | black, picked in the server sheet. `/api/art?size<=128` is now sent as bytes
  from Reef's origin (small LRU in `app.py`) so the canvas can read it.
- Tabs: Home · Library + Search FAB; `#kept` / `#downloads` redirect to `#library` (Downloads, Playlists + Import,
  New in Navidrome, Kept songs). Header: avatar (opens the server sheet) carrying the status dot.
- Errors from search / browse / home / library show typed hints (`errors.payload` on upstream 502s).
- **Test audio mode** (`testaudio.py`): `REEF_TEST_AUDIO=<file|dir>` serves local audio for YouTube streams (per-id
  hash pick, Range/206) and drops YouTube prefetches. Only when `REEF_HOST` is loopback (or `REEF_TEST_AUDIO_I_KNOW=1`);
  startup logs "TEST AUDIO MODE"; Now Playing's pill says "Test audio". Use it for all UI checks on the Mac.

### UI remake step 2: Player core (2026-10-22, uncommitted on `dreamingofu/codebase-overview-and-risk`)

- `player-core` (`PC` in `static/app.js`, node check `scratchpad/u2/player_core_check.js`): axis lock + the 24 px
  left-edge exclusion, rubber band, velocity, swipe / pull-up / close decisions, settle curve, seek fraction, ring
  offset, double-tap seek, neighbours, reorder / undo-remove, queue stats, marquee timing. `drag()` in app.js wires
  pointer events to it (followed on `window` once pressed; a drag swallows its click).
- **Mini player** (§3.1-3.2): 64 px card docked 4 px above the nav (28/12 corners, max 420), progress ring around
  round art + equaliser while playing, title marquee, white play + tonal next. Swipe sideways (layer behind shows the
  neighbour or "Nothing up next"), up/tap opens Now Playing, pull down pauses and hides it until something plays.
- **Now Playing** (§4): background styles Ambient / Immersive / Tint / Pure black (pref `reef.npbg`; picker sheet with
  live previews from ⋯ → Player background or the server sheet; the "Pure black" colours theme forces black), two
  cross-fading art layers (static blur of the 64 px copy). Top bar ⌄ · NOW PLAYING / "Playing from …" (taps back to
  that page; `fromRoute` saved in the session) · ⋯ (track menu: + Add to playlist…, Open on YouTube, Player
  background). Art track prev | current | next: swipe to skip (30% / flick), double-tap halves ±5 s (+5 per tap),
  blur-fill for non-square art, codec badge for FLAC. Like | Keep capsule (Like = `/api/likes`, long-press = add to
  a Reef playlist); long-press title/artist copies "Artist – Title". Div seek bar (14→22 px, thumb, stop dot, bold
  elapsed while scrubbing, arrow keys). Stretchy transport (skips fire 180 ms after the tap). Action row AirPlay
  (only with `webkitShowPlaybackTargetPicker` + an available target) · Lyrics | Shuffle | Repeat | Queue | Sleep (minutes
  left) · ⋯. The old bottom row, header YouTube link and share button are gone (both in ⋯ now).
- **Up next** (§4.7, §7): the old queue sheet is now a pull-up inside Now Playing (peek "Up next · title"; drag/flick,
  Queue button, ⌄, Escape, or pull the list down at its top). History dimmed above, current row on the tonal colour,
  "Next from …", "Autoplay suggestions". Swipe left = Play next, right = Remove + Undo toast; handle drags to reorder
  within its block. Header: Shuffle · Clear · Save as playlist (prompt for a name) · Autoplay switch.
- Not done (later steps): mini→full morph / FLIP (§3.3), squiggly seek, long-press transport preview, separate
  "Next in queue" section for Play-next items (needs a flag on queue entries), wide layout.
- **Check on the iPhone** (WebKit headless can't tell): gesture feel vs Safari's back swipe (left 24 px) and the
  Home Screen app; that the list pull-down at the top beats iOS's own scroll; swipe-to-skip / delayed skip while
  *paused* still starts playback (relies on WebKit forwarding the tap to a ≤ 1 s timer); blur + two-layer cross-fade
  cost on long sessions; locked-screen playback, mediaSession and the sleep timer unchanged; AirPlay button appears and
  opens the picker; Safari-tab height (~550 px on a 375 × 667 phone) leaves a usable art size.

### UI remake step 3: Lyrics (2026-10-22, uncommitted on `dreamingofu/codebase-overview-and-risk`)

- Lyrics are now a **full-screen view** (`#lyv`, inside `#np`, over the queue sheet) instead of replacing the art.
  Opens from the action row's Lyrics toggle or a single tap on the art (after the 380 ms double-tap window); rises from
  the bottom (scale .92 × .78, 16% lower, radius 32 → 0). Closes with ⌄, the top pill, a pull down on the top, or
  Escape. `reef.lyricsOn` still remembers it (reopening the player comes back to it). Background: `--c-base` 30%
  toward black, plus the static blurred 64 px art when the player background is Ambient / Immersive.
- `lyrics-view-core` (`LV` in app.js, node check `scratchpad/u3/lyrics_view_core_check.js`): rows (lines + interlude
  gaps: intro ≥ 4 s, blank / ♪ lines ≥ 4 s, and with word timing the silence after a line's last word), active row /
  word, word fill, dot progress, depth classes + the range that can change, 35% anchor, 20 ms stagger, long-line
  shrink, syllable groups. `lyrics-core` (`LY`) is unchanged.
- Depth: classes `d0`..`d4`, `d5` (±5..±8), `d9` (beyond ±8) (+ `past`): opacity 1 / .52 / .30 / .18 / .10 / .06,
  scale 1.05 / .95, blur 2 px for ±1 and 5 px for ±2..±4, none from ±5 (at .06, effectively invisible) and `d9`
  `visibility: hidden` outside a manual scroll: at most 8 blurred rows (static, never animated), opacity in 330 ms / out 500 ms; only the rows whose class changes are touched. Active
  row at 35%; rows glide there by FLIP on `translate` in a 20 ms wave (big jumps land). Line bounce: words pop in turn
  (40 ms) on line-timed lines. Word fill (BetterLyrics `words`): `background-clip: text` gradient with a .9em edge,
  sung words lift 3 px, words held > 1.1 s glow. Interlude dots fill in turn, pulse 1.6 s, fade 650 ms before the next
  line. One rAF loop only while the view is visible + playing + the ACTIVE row is a word-timed line or a gap
  (`LV.needsFrames`; mixed-timing lyrics don't run it on line-timed rows; one `--wf` / `--gp` write per frame); otherwise `timeupdate`. Nothing is touched while the view is hidden.
- Manual scroll (any scroll that isn't ours) pauses auto-follow for 3.5 s after it stops, sharpens the other rows and
  shows **Resync**; tapping a row seeks there (+ offset). States: skeleton, plain ("Not synced" note, 21 px / 1.75),
  instrumental, no lyrics (+ Find lyrics), error (+ Retry; non-404 failures aren't cached). ⋯ opens a light sheet:
  source, Timing −0.5 / value (reset) / +0.5, **Lyrics only** (hides the pill + bar, `reef.lyricsOnly`), Wrong
  lyrics? → the Find lyrics sheet (results now a joined block). Reduced motion: fade only, no zoom / glide / pops.
- Also: `.np` / `.lyv` are `overflow: clip` (focus or scroll-into-view can't scroll them); `drag()` counts the release
  point in the velocity (a finger that stopped before lifting no longer reads as a flick); tall sheets keep the
  Done button's height.
- **Check on the iPhone**: word fill smoothness and battery/heat with the rAF loop on a long word-timed song; that the
  2 / 5 px blur on up to 8 rows plus the static background blur stays smooth while rows glide; momentum scroll → Resync
  timing; `background-clip: text` rendering of word gradients (and the glow) in Safari; the open transition at 60 fps;
  single tap on art vs double-tap seek feel; safe areas of the pill / bar in the Home Screen app.

### UI remake step 4: Sheets (2026-10-22, uncommitted on `dreamingofu/codebase-overview-and-risk`)

- `sheet-core` (`SH` in `static/app.js`, node check `scratchpad/u4/sheet_core_check.js`): the track menu's item rules
  (`menuModel`, `canFix`, `fixKey`), row swipe thresholds (115 px or 45% of the row, flick 900 px/s past the 60 px band),
  swipe prefs, sheet drag-to-dismiss (min(140 px, 35%) or a 700 px/s flick), sleep timer numbers (clock parts, steppers,
  +5 min capped at 12 h) and Song info formatting.
- **Sheet system**: one stack (`sheetStack`; each sheet above the last, z-index 60 + depth), Escape / scrim close the top
  one, focus moves into a sheet and back to its opener (or the same song's ⋯ after a list repaint), Tab wraps in the
  top sheet, the top of every sheet drags it down (pointer events via `drag()`, rubber up), touchmove outside a
  sheet's scrolling parts (`[data-scroll]`, `.sheet-body`) is cancelled so the page behind can't scroll, 40 × 4
  grabbers. Reduced motion: sheets fade, sub-pages swap, rows jump back.
- **Track menu** (§8.1): a floating card (inset 10, radius 24, scrim .72, max 85% height, ✕) tinted with the song's
  cover (`--c-*` set on the card from `paletteFor`; neutral for Accent only / Pure black). Header: 56 px art, title,
  artist, source badge (YouTube / Monochrome · FLAC / Library / Not matched yet / Replacement), Share + Info. Quick grid
  (3 columns, 2 for 2 or 4 tiles): Play next · Add to queue (Remove from the queue) · Start radio · Keep (In library /
  Keeping…) · Add to playlist · Like (Liked). Joined groups: Go to (artist; a picker sub-page with several; album, not on
  that album's own page) · Recommendations (Not interested, Hide <artist>; both toast with Undo) · More (Song info, Fix
  wrong match, Copy link, Open on YouTube, Player background in the player). Placeholders get Play next / Add to queue /
  Song info / Fix only; library (`nd:`) tracks no radio / dislike / copy / fix; Monochrome no radio / copy. Add to
  playlist is a sub-page with an inline "New playlist" field (no `prompt()`; Save as playlist in Up next still prompts).
  Opens from ⋯ (search, Home cards / Quick picks, album / artist / playlist / import pages, Kept songs), a 500 ms long
  press on any song row, card, poster, top result or Up next row (the release's click is swallowed; right-click too),
  and Now Playing's ⋯ / long-press Like (straight to the playlist picker). Play next / Add to queue from the menu and
  from swipes toast with Undo (the song leaves the queue again, or goes back where it was).
- **Row swipes** (§8.2) on `.rows > .row.trk` (not the Quick picks grid, which scrolls sideways; `touch-action: pan-y`):
  default right = Play next, left = Add to queue; Server sheet → Row swipes picks Next / Queue / Like / Keep / Off per
  direction (`reef.swipe`). A coloured band with the icon opens under the row, the label shows once armed; an action
  that can't apply (the playing song, a placeholder's Like / Keep, a kept song's Keep) only resists. A mouse drag on a
  row's link / art no longer starts a native drag (it ended swipes and presses).
- **Song info** (§8.3): Details, Technical, Ids (copy buttons), Copy link / YouTube. New `GET /api/stream/<id>/info`
  never resolves: the resolve cache's codec / bitrate / sample rate / size / client (YouTube resolves now record
  yt-dlp's `acodec` / `abr` / `asr`; an itag table fills older entries), the kept file (path relative to the music
  folder, format, size, codec, sample rate, bits, ReplayGain via mutagen; the Monochrome upgrade of a YouTube keep
  too), `test_audio`. Lyrics: the provider only when already loaded (no lookup).
- **Fix wrong match** (§8.4): for placeholders, songs an import / the placeholder lookup matched (they now carry
  `mkey`, the match key, through `slim`), dead-track replacements, and the song in the player / queue. Paste a YouTube /
  YT Music link, or pick from new `POST /api/match/candidates` (key + current pin + YT Music's top 5 with raw and
  ranked scores; the key comes back even when the search fails). `PUT /api/match/<key>` pins it; the queue entry is
  swapped in place (the current one plays on), an import page refetches (its row materialises).
- **Sleep timer** (§8.5): 15/30/45/60 chips, End of this song, Custom hour / minute steppers (`reef.sleepCustom`);
  running: Pausing at <time>, HH:MM:SS boxes (only the digits update, once a second while open), progress bar, +5 min,
  Stop, Change the timer. The Now Playing toggle shows minutes left inside a ring of the share left. The deadline
  logic (wall clock, 5 s checks, fade where volume works) is unchanged.
- **Check on the iPhone**: long press vs iOS text selection / link previews (`-webkit-touch-callout: none` on rows,
  cards); row swipes vs vertical scrolling and Safari's back swipe; sheet drag-down from the header card vs taps on
  Share / Info; the touchmove guard (no page scroll behind sheets, lists inside still scroll); `inert` sub-pages and
  focus return with VoiceOver; copy buttons over plain http (textarea fallback inside the tap).

### API shapes (step 4)

- `GET /api/stream/<id>/info` → `{id, source, stream: {client, itag, mime, ext, codec, bitrate (kbps), sample_rate,
  duration, size, ttl_s} | null, kept: {id, kept_at, nd_id, upgraded_from, file?, format?, exists?, size?, codec?,
  bitrate?, sample_rate?, bits?, channels?, length?, replaygain?} | null, test_audio}`; 404 for a malformed id.
- `POST /api/match/candidates {title, artist?, duration?, album?, key?, q?}` → `{key, pinned (track) | null,
  candidates: [{track, score, ranked}], error?}`; 400 bad input.

### UI remake step 5: Discovery (2026-10-22, accepted by root Codex; local commit on `dreamingofu/codebase-overview-and-risk`, not pushed or deployed)

- `discovery-core` (`DC` in `static/app.js`, node check `scratchpad/u5/discovery_core_check.js`): search groups (§10.1
  order, top result left out of its group, previews + "Show all"), filter chips per source, page sort / filter
  (`viewTracks`: accent-folded words over title / artist / album; stable name / artist sort), Home plan (moods out,
  Quick picks first, server order, device-local Jump back in), the cookie path + mask, `/api/thumb` URLs, Google art
  resizing, header progress / parallax, collection lines, recent-search drop / undo, Keep album progress.
- **Search** (§10.1): YouTube searches are one `GET /api/search/all` (other sources keep the songs-only search). Top
  result card (radius 28, cover colours, cookie art for artists; songs Play / Keep / ⋯, artists Shuffle + Radio = YouTube's
  RDAO / RDEM mixes via `/api/playlist`, albums / playlists Play + Shuffle; the card opens the page), then joined blocks
  Songs · Artists · Albums · Videos · Playlists with "Show all" = the chip. Chips Top results · Songs · Videos · Albums ·
  Artists · Playlists · In library (fetched once per query: songs = the 20-song search, others
  `/api/search?filter=`). Background glow from the top result. No results: "Search Monochrome instead" (or YouTube).
  Landing: Recent (✕ or long press removes, Undo), Charts cards (one per `/api/charts` playlist → `#playlist/<id>`),
  moods tiles. Suggestions get a SUGGESTIONS label; the magnifier spins while a search / suggestions load. The bar's
  home button is now a back arrow (history back, else Home). Pasted links unchanged.
- **Album / playlist / Reef playlist pages** (§10.2): one `collectionPage` for `#album`, `#playlist`, `#local`. Page tinted
  from the cover (`/api/thumb`, else the first song's `/api/art`): base 45-55% toward black, accent glow .45, the 64 px
  copy blurred at .35; centred art (55% width, 180-250, radius 24, coloured shadow + rim, blur-fill for 16:9 thumbs,
  shrinks a little as it scrolls away); type · year, title, artist links (accent), songs · length, 2-line description
  with Show more; Play | Shuffle joined (24 / 8 radii); Save (YouTube pages → a Reef playlist; local → Pin) · Keep (28 px
  ring of the share kept) · Share (YouTube Music link) · ⋯ (page menu card: Play next / Add to queue all shown, Go to
  artist, Save, Copy link, Open on YouTube Music; local: .m3u) · Sort (playlists: Default / Name / Artist chips, a filter
  field over 12 songs; what's shown is what plays). Joined numbered rows on albums. The header stays clear over the
  tint until the title scrolls under it.
- **Artist page** (§10.3): full-bleed hero under the clear header (min(W, 400) + safe area, bottom radius 48, the
  plan's shade, parallax .4), name 30-36/800 + "N monthly audience · N subscribers"; Play (white pill) · Shuffle ·
  Radio · Hide · Share in a sideways row with edge fades; Top songs (joined, Show all) · Albums · Singles & EPs ·
  Videos (16:9) · Similar artists (cookie) · About; colours from the photo. Follow is not shown (needs a follow list).
- **Home** (§9 1, 2, 4, 7): greeting, mood chips (YT Music moods → mood pages, + All), Quick picks as joined 4-row
  columns (50 px art, 16/700 + 14/600, ⋯ per row; the + moved to the menu), then the server's shelves in its order,
  including Charts (rank badges, See all → the chart playlist) and YouTube Music's `ytm:*` shelves (tracks → cards,
  `cards` → typed cards with an Album / Playlist pill, round artists); cards are 150 px, radius 13. Recent searches and
  the moods grid left Home (Search has them; the grid stays when YT Music browse is off).
- Lists: `listItems(name)` resolves `h:<shelf>` / `hc:<shelf>` (Home) and the search / page lists; `startQueue()` plays a
  list straight from data (cards, mixes) with its own "Playing from".
- **Check on the iPhone**: the clear header over tinted / full-bleed pages (safe area, status bar legibility), hero
  parallax and the art shrink while momentum-scrolling, the cookie mask (data-URI SVG `mask`) on Safari, horizontal
  scrollers next to the back-swipe edge, the filter field and the keyboard, long press on recent searches vs text
  selection, glow + blurred backdrops' cost.

- **Supervision / state (2026-10-22)**: root Codex (Orca terminal `term_d731eb62-3bf8-4362-ad78-88c7e09366c4`) owns
  review and acceptance; Claude (`term_bc7a30e2-cfdf-433d-95e9-470cd4a9d3f2`) implemented U5. **U5 accepted by root
  Codex** and committed locally (one commit on top of `f8f09cd`, worktree `/Users/felipe/orca/workspaces/reef/larvacean`,
  branch `dreamingofu/codebase-overview-and-risk`). **Not pushed, not deployed** at the time: origin/main and the phone
  stayed on `f8f09cd`; root authorized the local commit only, not publication (superseded later: the owner authorized
  the push and Android install of `9d71366`, see CURRENT). U6 and further feature work are on hold.
  Root acceptance checks: pytest 983 passed, 1 deselected (16.36 s); Ruff, JS syntax (`node --check`), discovery-core 17
  checks and `git diff --check` pass; root's isolated WebKit runs: `cache-stale-blocklist.js` →
  `{hidden: true, playlistCalls: 3, queuedTitles: []}`, failed-refresh queue empty, ✕-while-pending idle
  (`{cleared: "", idle: true, spinnerBusy: false}`), "In library" full songs, filter Retry semantics, artist-video kept
  state. Independent Codex review `review-codex-261564587e1a` reused (its 4 findings fixed) plus root-rendered evidence.
  Real-device gaps for U5 are the "Check on the iPhone" list above.
  Next: root-led codebase audit (report `/tmp/reef-codebase-audit-20261022.md`; first audit task settled; read-only
  follow-up in `term_62492f92-3dea-4cc5-ac14-f76f76a4bdb7`, Run `run_644fd9908c5b`, Task `task_3936c3abab7f`, Dispatch
  `ctx_fa0437ae058d`), then bounded refactor tasks that root assigns to a fresh Claude session. After the U5 commit
  this session relinquishes checkout writer ownership.
- **Post-U5 audit queue (root-reproduced, not yet fixed)**:
  - `updater.check` (~L160-179) snapshots `update.json`, makes its network calls, then saves the stale full dict,
    erasing a run record `updater.start` wrote meanwhile; that removes the active-update guard, so updates can
    overlap. Fix later: merge only the check-owned fields into the latest JSON under the lock, with no network calls
    while the store lock is held.
  - `backup.validate` accepts a playlist with `updated_at: "oops"`; `restore(mode=replace)` succeeds, then
    `playlists.all_playlists` raises TypeError (unary minus on a str). Validation must reject malformed metadata and
    non-finite timestamps while good backups stay compatible.
  - `store.seconds(10**400)` raises OverflowError instead of returning None.
- **Review repairs (root)**: a failed filter chip (e.g. Albums 502) shows its error and waits for Retry (it used to
  re-ask on every paint: `DC.kindAction`); a card's Play / Shuffle / Radio re-asks a page that a hide invalidated or
  that expired (`loadPage` decides) and, if only an old copy is left, drops hidden artists' songs; "In library" asks
  for the full songs search itself (`DC.keptPool` over the preview, those songs and the videos); artist-page videos
  mark kept / liked; the search's top artist count is labelled "monthly audience" (`audience`, server; ytmusicapi
  calls it `subscribers`); ✕ while a search is pending resets `searching` (`DC.clearedSearch`), so the spinner stops
  and the late answer is still dropped as stale.
  Follow-up (root msg_305d39f286a9): a hide / unhide the server confirmed (POST / DELETE 2xx) updates `state.blocked`
  at once (`DC.blockAdd` / `DC.blockRemove`), whether or not the next GET /api/blocklist works.
  Last repair (root msg_cf0e2a3ac9bd): a page a hide / unhide invalidated whose refresh then failed is never played
  from (top card, Play / Shuffle, rows: an error toast instead; `DC.playableCopy`), and a GET /api/blocklist asked
  before a later local hide / unhide is dropped (`blockGen`, `DC.genCurrent`); a merely expired copy still plays
  minus hidden artists, a fresh one without a refetch, a successful refresh as usual.

### API shapes (step 5)

- `GET /api/search/all?q=` → `{top, songs, videos, albums, artists, playlists, fallback?}`: songs / videos are marked
  tracks (videos `video: true`); cards `{type: album|artist|playlist, id, title, subtitle, thumb, …}` (albums `artists,
  year, kind`; artists `audience` (monthly audience, top result only), `radio_id, shuffle_id`; playlists `author`,
  "VL" dropped); `top` is a card or
  `{type: "track", track}`. Hidden artists filtered (artist cards, albums by them, their songs); cached 10 min per
  query. YouTube Music off / failing → the YouTube source's songs search, `fallback: true`; 502 only when that fails.
- `GET /api/search?q=&filter=videos|albums|artists|playlists` → that kind (tracks or cards); 400 unknown filter, 502 YT
  Music off / failing. No `filter` (or `songs`) = the old songs search.
- `GET /api/thumb?u=<https URL>&size=64` (≤ 128): a small copy of a YouTube Music / YouTube image from Reef's origin
  (cover colours). Only https on `*.googleusercontent.com`, `*.ggpht.com` (no query) or `i.ytimg.com` (+ i1-4, i9),
  default port, no credentials; redirects not followed; JPEG / PNG / WebP / GIF only (never SVG / HTML), ≤ 512 KB,
  shared LRU with `/api/art`'s tiny copies; `nosniff` + `default-src 'none'`. 400 refused URL, 404 upstream failure.
- `/api/artist/<id>` adds `videos` (marked tracks, `video: true`), `subscribers`, `monthly_listeners`, `radio_id`,
  `shuffle_id`; `/api/album/<id>` and `/api/playlist/<id>` add `description` (+ playlist `track_count`); Home's Charts
  shelf adds `playlist_id`.

## Audit batch 1 (2026-10-22): persistence correctness (accepted by root Codex; local commit)

Audit report: `/tmp/reef-codebase-audit-20261022.md` (findings F1, F5, F6). Base `bd5014d`; changed files: `backup.py`,
`store.py`, `updater.py`, `tests/test_backup.py`, `tests/test_library.py`, `tests/test_listens.py`, `tests/test_updater.py`.

- `updater.check` (F5): the GitHub / PyPI calls still run outside the file lock, but the save re-reads update.json under
  `_file.lock` and only replaces `github` / `pypi` / `compare`. An update `run` recorded while the check waited is kept,
  so `POST /api/update` still answers 409 while it runs (before: the check's stale copy erased `run`).
- `store.seconds` (F6, store half): an int too big for a float (`10**400` in a JSON body) is an invalid length (None)
  like nan / inf: `PUT /api/likes`, `POST /api/playlists`, `POST /api/listens` (400) no longer 500. `app._seconds`
  (`/api/match`, keep hints) still lacks the same catch: app.py was out of scope for this batch.
- Restore (F1), `backup.py`:
  - `_num` accepts only finite numbers, so JSON `1e400` / `Infinity` / `NaN` / huge ints can't get through.
  - Timestamps must be epoch seconds in [0, 1e11).
  - History, likes, listens and dislikes items are `store.clean_track` plus per-section field checks:
    - history: `file` (exists here), `kept_at`, `nd_id` / `nd_cover` (`[\w.-]{1,128}`), `thumb` / `art` (http(s) or ""),
      `upgraded_from`, `lyrics`, `gain_db`;
    - listens: `played_s` < 86400 and `started_at` (both required), `ended_at`, `counted` / `skipped` / `scrobbled`
      booleans, `via`;
    - likes: `liked_at`;
    - dislikes: `at` (required), `until`, `artist_key`, `artist_id`, `blocked`.
  - A bad field is dropped and the item kept; fields Reef doesn't know travel only as plain scalars.
  - Playlists: the name is trimmed (blank = skipped); `pinned` (default false), `created_at` / `updated_at`, `description`,
    `exported` (a relative `.m3u` path, no `..`) and `import` (`kind`, `rows` / `skipped` / `total` ints, `at`) are
    checked; `cover_id` is recomputed as `playlists._touch` does; duplicate ids are skipped (history, likes, dislikes
    too), at most `PLAYLISTS_MAX`.
  - Lyrics: `at`, packed `words`, the `lrclib` copy and a pin's `lrclib_id` are checked. Matches: `at` / `score` finite.
  - The merge sort keys never raise, even on junk already in the stores from before this fix.
  - `restore()` first merges every section into a copy of the current store (a section that can't apply refuses with
    400 "nothing was changed", no snapshot written), then snapshots and writes each store, merging again against that
    store under its own lock. A write failing midway raises `backup.RestoreIncomplete` (a `BackupError`, so today
    a 400): its message names the sections already restored and `backups/pre-restore-…json`. The stores are separate
    atomic files; the restore as a whole is not atomic.
- Not done here (later batches): data written before this fix stays as it is until a restore replaces it
  (`playlists.all_playlists` still sorts on a raw `updated_at`); app.py `_seconds` OverflowError, JSON-array bodies,
  the Origin check, keep / .m3u name collisions, credential redaction, the committed JS checks, updater health with
  `REEF_OPEN`.
- Checks (Mac, scratch data, no network):
  - `python -m pytest -q tests/test_backup.py tests/test_backup_coverage.py tests/test_updater.py tests/test_library.py
    tests/test_listens.py`;
  - full `python -m pytest -q`: 1003 passed, 1 deselected;
  - `ruff check .` and `git diff --check`: clean;
  - the five new restore tests, the `store.seconds` tests and the updater test were also run against `bd5014d`'s
    backup.py / store.py / updater.check (a scratch pytest plugin loading the old code): all of them fail there.

## Audit batch 2 (2026-10-22): keep and .m3u name collisions (accepted by root Codex after repairs; local commit)

Root acceptance: an independent final diff review; focused tests (downloader, lossless, library, lyrics) 150 passed;
full suite 1020 passed / 1 deselected; Ruff and diff check clean; root scripts `ytdlp-intermediate.py` and
`file-ownership.py` in `/tmp/reef-codex-refactor-20261022/` pass.


Audit findings F3 and F9 (`/tmp/reef-codebase-audit-20261022.md`). Base: batch 1 commit `8fc8de9`. Changed files:
`downloader.py`, `playlists.py`, `lyrics.py` (sidecar write only), `tests/test_downloader.py`, `tests/test_library.py`,
`tests/test_lyrics.py`.

**Root review of the first version (blockers, now repaired):**
- **F3 intermediates.** The first version only predicted the final file name. Repro with the real installed yt-dlp
  and FFmpeg (only `extract_info` swapped for a local result; `/tmp/reef-codex-refactor-20261022/ytdlp-intermediate.py`):
  - source `.wav` 1 s; an unknown `DOWNLOAD_DIR/Title.wav` 4 s already there;
  - the keep reused that 4 s file as its download, produced a 4 s `.opus` and deleted `Title.wav`.
- **Alias ownership.** `_keep_path` excluded other tracks' files by abspath string but matched its own file with
  `samefile`. Repro:
  - track `aaaaaaaaaaa` owns `p/Title.opus`;
  - track `bbbbbbbbbbb`'s history points at `p/Alias.opus`, a symlink to that file;
  - candidates `[p/Title.opus, p/Title [bbbbbbbbbbb].opus]` returned `p/Title.opus`, which authorised overwriting
    the other track's file.
- **Playlist exports.** Ownership was compared as raw relative strings, so a link or another case spelling of another
  playlist's export was missed.
- **Export `.tmp` link** (root repro): a kept `Track.opus`, and `Mix.m3u.tmp` as a symlink to it. Exporting "Mix"
  wrote the playlist text through the link into the song and published the link as `Mix.m3u`.
- **`.lrc` sidecar link** (root repro, real opus): a re-keep of the same id, with `Title.lrc` a symlink to another
  file. `lyrics.embed` wrote the sidecar through the link and corrupted that file.

**Behaviour now:**
- **Staging.** Every keep (yt-dlp, generic, lossless) works in its own `tempfile.mkdtemp` folder under `TMP_DIR`, which
  is removed when the keep ends.
  - yt-dlp's `paths` home and temp both point there, so the download, intermediates, the thumbnail
    (convert → square crop → embed) and the audio extraction all happen inside it. The postprocessor order is
    unchanged, and there is still one `extract_info(..., download=True)` per keep.
  - Generic keeps fetch and convert in their stage too.
  - Nothing outside the stage is read, replaced or deleted except the final destination.
- **Publish.** `downloader._publish` picks the destination with `_keep_path`, then moves the staged file into a
  `.part` created exclusively by `mkstemp` (`<stem>.<random><ext>.part`, so an existing `*.part` or a symlink named
  like one is never touched), then `os.replace` into place. A failure unlinks only that `.part`.
- **Names.** `<title><ext>` (the yt-dlp name is the one yt-dlp itself sanitised in the stage), then
  `<title> [<id>]<ext>`, then `(2..50)`.
- **Ownership** (`_is_same` / `_may_be_same`):
  - A path is taken when another kept track's file is, or could be, that file: the same existing file through a
    link or alias, or the same resolved path compared case-insensitively. The case-insensitive comparison errs
    towards "taken"; that only costs a suffix.
  - Own reuse (re-keep / force) needs the exact same file, and only when no other track's file is or could be it.
  - A free name also needs no `<stem>.lrc` next to it, so the lyrics sidecar of something else is never overwritten.
  - Ordinary keeps keep their plain names. A file that two history entries share from before the fix is never
    rewritten.
- **Exports** (`playlists.export`):
  - The same `_is_same` / `_may_be_same` checks run on the absolute paths of the recorded exports. Another
    playlist's export as a link or another case spelling counts as taken.
  - The playlist's own export is reused only when it isn't taken.
  - The old export is deleted only when it is the playlist's own (not taken) and isn't the new file.
  - `_export_lock` still covers pick, write and record (lock order `_export_lock` → store lock). A playlist deleted
    during its export leaves no file (404).
- **Exclusive temporary writes.** The `.m3u` (`playlists._write_new`) and the `.lrc` sidecar (`lyrics._write_new`) are
  written to a hidden temp file created with `O_CREAT | O_EXCL` (a random name; an existing file or link of that
  name is never opened), then `os.replace`d onto the final name. That replaces a link or hard link there instead of
  writing through it, and the temp file is removed on failure. Contents, names and APIs are unchanged.

**Checks:**
- **Root script:** `PYTHONPATH=. .venv/bin/python /tmp/reef-codex-refactor-20261022/ytdlp-intermediate.py` now passes:
  saved 1.0 s, the untracked 4 s `Title.wav` unchanged.
- **New tests, `tests/test_downloader.py`:**
  - `test_real_ytdlp_processing_never_reuses_or_deletes_files_it_didnt_make`: real yt-dlp + FFmpeg; local `file://`
    source and cover through `YDL_EXTRA` `enable_file_urls`; skipped without ffmpeg. Checks a 1 s result with an
    embedded cover, and that the existing `Title.wav` / `Title.jpg` / `Title.webp` in the library and the `Title.wav`
    / `Title.temp.opus` in `TMP_DIR` keep their bytes, and that the stage is removed.
  - `test_an_alias_of_another_tracks_file_is_not_this_tracks_own`: the root's alias case; the other file's bytes
    are kept.
  - `test_files_already_named_like_temp_parts_are_left_alone`: an existing `.part`, a `.part` symlink, an unrelated
    `.lrc`.
- **New tests, `tests/test_library.py`:** `test_an_alias_of_another_playlists_export_is_not_ours[link|case]`, and
  `test_a_leftover_named_like_the_temp_file_is_never_written_through` (the root's `.tmp` symlink case: the song's
  bytes are kept, `Mix.m3u` is a regular file, the leftover is untouched).
- **New test, `tests/test_lyrics.py`:** `test_embed_sidecar_never_writes_through_a_link[symlink|hardlink]`.
- **Unchanged batch 2 tests:** the earlier fake-yt-dlp tests now expect the single `extract_info(download=True)`
  per keep (was `process=False`); their file and bytes assertions are unchanged.
- **Mutation checks:**
  - All five new cases fail against `8fc8de9`'s downloader.py / playlists.py.
  - The alias and `.part` tests fail with the first version's `_keep_path`.
  - The intermediate case fails on the first version (the root's script).
  - The `.tmp` and `.lrc` link tests fail on `8fc8de9`'s playlists.py / lyrics.py.
- **Suite:** full `python -m pytest -q` 1020 passed, 1 deselected; `ruff check .` and `git diff --check` clean.

**Gaps and risks:**
- Not run against real YouTube or on the phone (the `-m live` test only resolves).
- Keeps now copy the finished file from `TMP_DIR` to shared storage: one extra copy of a few MB, where yt-dlp used
  to move it there itself.
- Playlists still leave their old export file when deleted (as before).
- F10 (job cap) is not done.

## Ideas / next steps

- Check round 2 on the iPhone: Now Playing on the small screen, tile artwork, source chips, FLAC playback over cellular.
- Run `scripts/bench_resolve.py` on the phone and record the numbers here.
- Save the playback queue across reloads; add a lyrics view.
- A supervisor loop for Navidrome too (same idea as `termux/reef-run.sh`; out of scope for Round 9).
- Change the weak Navidrome admin password (Reef only needs it for `startScan`).
