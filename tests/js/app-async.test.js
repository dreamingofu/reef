"use strict";
// Async regressions for static/app.js outside the pure cores: the fetch wrapper `api`, Fix wrong match (openFix /
// runFixSearch / pinFix / applyFix) and a backup restore with the reads it refreshes. The shipped functions run
// through harness.scope over small stubs; every API reply is a deferred promise, so each test picks the order requests
// finish in. Stubs stand in only for the DOM, the server and the rest of the app.
const nodeTest = require("node:test");
const assert = require("node:assert/strict");
const { load, scope } = require("./harness");

const M = load("match-core"), SH = load("sheet-core"), DC = load("discovery-core");

function deferred() {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject };
}
// Lets every promise chain that can move on do so.
const flush = () => new Promise((r) => setImmediate(r));
// A test that waits on a reply it never gets fails after 5 s instead of hanging the run.
const test = (name, fn) => nodeTest(name, { timeout: 5000 }, fn);
const fail = (status, message = `HTTP ${status}`) => Object.assign(new Error(message), { status, body: {} });

// An `api` stub: every call waits for the test to answer it.
function server() {
  const calls = [];
  const api = (path, opts = {}) => {
    const d = deferred();
    calls.push({ path, method: opts.method || "GET", body: opts.body, ...d });
    return d.promise;
  };
  const matching = (path, method) => calls.filter((c) => !c.answered && c.method === method
    && (typeof path === "function" ? path(c.path) : c.path === path));
  const pick = (c, what) => { assert.ok(c, `no unanswered ${what}`); c.answered = true; return c; };
  return {
    api, calls,
    take: (path, method = "GET") => pick(matching(path, method)[0], `${method} ${path}`), // the oldest
    latest: (path, method = "GET") => pick(matching(path, method).at(-1), `${method} ${path}`),
    count: (path, method = "GET") => calls.filter((c) => c.method === method && c.path === path).length,
    puts: () => calls.filter((c) => c.method === "PUT").map((c) => [c.path, JSON.parse(c.body).id]),
  };
}

// `$` over plain objects: one per selector, made on first use.
function dom() {
  const els = new Map();
  return (sel) => {
    if (!els.has(sel)) {
      const cls = new Set();
      els.set(sel, { value: "", innerHTML: "", hidden: true, dataset: {}, focus() {}, blur() {},
        classList: { add: (c) => cls.add(c), remove: (c) => cls.delete(c), contains: (c) => cls.has(c) } });
    }
    return els.get(sel);
  };
}

// ---------- api ----------

function apiWith(response) {
  const seen = [];
  const s = scope(["api"], { fetch: async (path, opts) => { seen.push([path, opts]); return response(); } });
  return { api: s.api, seen };
}
const reply = (body, init) => () => new Response(body, init);

test("api: a 2xx JSON body is returned as is, with the request passed through", async () => {
  const opts = { method: "POST", body: "{}" };
  const { api, seen } = apiWith(reply('{"ok":true,"n":[1,2]}', { status: 201 }));
  assert.deepEqual(await api("/api/x", opts), { ok: true, n: [1, 2] });
  assert.deepEqual(seen, [["/api/x", opts]]);
});

test("api: 204 resolves null", async () => {
  assert.equal(await apiWith(reply(null, { status: 204 })).api("/api/x"), null);
});

test("api: a 2xx whose body isn't JSON rejects (never an empty success)", async () => {
  for (const body of ["<html>proxy page</html>", "", "{\"cut"]) {
    const err = await apiWith(reply(body, { status: 200, statusText: "OK" })).api("/api/x").then(
      (v) => assert.fail(`resolved ${JSON.stringify(v)} for ${JSON.stringify(body)}`), (e) => e);
    assert.ok(err instanceof Error);
    assert.equal(err.status, 200);
    assert.match(err.message, /JSON/);
  }
});

test("api: a typed error keeps its message, status and payload", async () => {
  const body = { error: "YouTube wants a sign-in", block: "BOT_WALL", hint: "Try later" };
  const err = await apiWith(reply(JSON.stringify(body), { status: 503, statusText: "Service Unavailable" })).api("/api/x").catch((e) => e);
  assert.equal(err.message, "YouTube wants a sign-in");
  assert.equal(err.status, 503);
  assert.deepEqual(err.body, body);
});

test("api: a non-2xx without a usable JSON object still throws its status, not a parse or type error", async () => {
  for (const [body, statusText, message] of [
    ["", "Bad Gateway", "Bad Gateway"],
    ["<html>502</html>", "Bad Gateway", "Bad Gateway"],
    ["null", "Not Found", "Not Found"],
    ["[1]", "Conflict", "Conflict"],
    ["", "", "Request failed (502)"],
  ]) {
    const status = statusText === "Not Found" ? 404 : statusText === "Conflict" ? 409 : 502;
    const err = await apiWith(reply(body, { status, statusText })).api("/api/x").then(() => assert.fail("resolved"), (e) => e);
    assert.equal(err.message, message, `body ${JSON.stringify(body)}`);
    assert.equal(err.status, status);
    assert.deepEqual(err.body, {});
  }
});

// ---------- Fix wrong match ----------

// The shipped Fix functions with the real queue swap (swapEverywhere over M.swap): picks land in `state.queue`.
function fixApp() {
  const srv = server(), $ = dom(), log = { toasts: [], closed: [], loads: 0, changed: 0 };
  const state = { queue: [{ id: "playing", title: "Playing" }], index: 0, original: null, view: "home" };
  const s = scope(["fix", "fixQuery", "openFix", "runFixSearch", "pinFix", "applyFix", "swapEverywhere"], {
    M, SH, $, api: srv.api, slim: (t) => ({ ...t }), esc: String, art: () => "", fmt: String, skRows: () => "",
    renderFixTrack() {}, openSheet() {}, toast: (m) => log.toasts.push(m), noteKept() {},
    closeSheet: (sel) => log.closed.push(sel), state, cur: () => state.queue[state.index],
    load: () => log.loads++, queueChanged: () => log.changed++, loadPage() {}, detail: { kind: "" },
  });
  const busy = () => [s.fix.busy, $("#fix-sheet").classList.contains("busy")];
  const candidates = () => srv.calls.filter((c) => c.path === "/api/match/candidates");
  const queue = () => state.queue.map((t) => (t.mkey ? [t.id, t.mkey] : [t.id])); // [id, match key] per entry
  return { s, srv, log, state, busy, candidates, queue };
}
const ph = (id) => ({ id, q: true, title: `Song ${id}`, artist: "Someone" }); // an import placeholder: no key yet
const song = (id, mkey) => ({ id, title: `Song ${id}`, channel: "Someone", mkey });

test("pinFix: two picks waiting on the same pending key send one PUT", async () => {
  const { s, srv, log, state, busy, queue } = fixApp();
  const t = ph("ph-1");
  state.queue.push(t);
  s.openFix(t);
  const a = s.pinFix("first-pick"), b = s.pinFix("second-pick");
  assert.deepEqual(busy(), [true, true]);
  srv.take("/api/match/candidates", "POST").resolve({ key: "k1", candidates: [] });
  await flush();
  assert.deepEqual(srv.puts(), [["/api/match/k1", "first-pick"]]);
  srv.take("/api/match/k1", "PUT").resolve({ track: { id: "yt-right", title: "Right" } });
  await Promise.all([a, b]);
  assert.deepEqual(srv.puts().length, 1);
  assert.deepEqual(queue(), [["playing"], ["yt-right", "k1"]]);
  assert.deepEqual([log.closed, busy()], [["#fix-sheet"], [false, false]]);
});

test("pinFix: a pick waiting on the old key never PUTs under the newly opened song's key", async () => {
  const { s, srv, log, state, busy, candidates, queue } = fixApp();
  const old = ph("ph-old"), neu = song("yt-new", "q:b|new");
  state.queue.push(old, neu);
  s.openFix(old);
  const pick = s.pinFix("old-selection");
  s.openFix(neu); // the sheet moves on before the old key arrived
  assert.equal(s.fix.key, "q:b|new");
  assert.deepEqual(busy(), [false, false], "the new sheet starts free");
  candidates()[0].resolve({ key: "k-old", candidates: [] });
  await flush();
  assert.deepEqual(srv.puts(), []);
  await pick;
  assert.deepEqual([log.closed, busy()], [[], [false, false]]);
  // The new sheet acts on its own.
  const next = s.pinFix("new-selection");
  await flush();
  assert.deepEqual(srv.puts(), [["/api/match/q%3Ab%7Cnew", "new-selection"]]);
  srv.take("/api/match/q%3Ab%7Cnew", "PUT").resolve({ track: { id: "yt-better", title: "Better" } });
  await next;
  assert.deepEqual(queue(), [["playing"], ["ph-old"], ["yt-better", "q:b|new"]]);
  assert.deepEqual([log.closed, busy()], [["#fix-sheet"], [false, false]]);
});

test("pinFix: reopening the same placeholder object is a new sheet: the old pick is dropped", async () => {
  const { s, srv, log, state, busy, candidates, queue } = fixApp();
  const t = ph("ph-1");
  state.queue.push(t);
  s.openFix(t);
  const pick = s.pinFix("old-pick");
  s.openFix(t); // the same object, opened again
  assert.equal(s.fix.t, t);
  candidates()[1].resolve({ key: "k2", candidates: [] }); // the new sheet's key lands first
  await flush();
  candidates()[0].resolve({ key: "k1", candidates: [] });
  await flush();
  assert.deepEqual(srv.puts(), []);
  await pick;
  assert.deepEqual([log.closed, busy()], [[], [false, false]]);
  const next = s.pinFix("new-pick");
  await flush();
  assert.deepEqual(srv.puts(), [["/api/match/k2", "new-pick"]]);
  srv.take("/api/match/k2", "PUT").resolve({ track: { id: "yt-right" } });
  await next;
  assert.deepEqual(queue(), [["playing"], ["yt-right", "k2"]]);
});

test("pinFix: an old PUT finishing after the same song's sheet reopened changes nothing; the latest pick wins", async () => {
  for (const { current, outcome } of [{ current: false, outcome: "success" }, { current: true, outcome: "success" },
    { current: false, outcome: "failure" }]) {
    const what = `${current ? "current" : "queued"} song, old PUT ${outcome}`;
    const { s, srv, log, state, busy, queue } = fixApp();
    const t = song("yt-a", "ka");
    state.queue.push(t);
    if (current) state.index = 1; // the song being fixed is the one playing
    s.openFix(t);
    const first = s.pinFix("pick-one");
    await flush();
    s.openFix(t); // the same song (and match key) opened again while the first PUT runs
    assert.deepEqual(busy(), [false, false], `${what}: the reopened sheet isn't held by the old PUT`);
    const second = s.pinFix("pick-two");
    await flush();
    assert.deepEqual(srv.puts(), [["/api/match/ka", "pick-one"], ["/api/match/ka", "pick-two"]], what);
    const old = srv.take("/api/match/ka", "PUT");
    if (outcome === "success") old.resolve({ track: { id: "pick-one", title: "First" } }); else old.reject(fail(502, "Upstream down"));
    await first;
    assert.deepEqual(queue(), [["playing"], ["yt-a", "ka"]], `${what}: the old answer doesn't touch the queue`);
    assert.deepEqual([state.index, log.loads, log.changed, log.closed, busy()], [current ? 1 : 0, 0, 0, [], [true, true]], what);
    srv.take("/api/match/ka", "PUT").resolve({ track: { id: "pick-two", title: "Second" } });
    await second;
    assert.deepEqual(queue(), [["playing"], ["pick-two", "ka"]], what);
    assert.deepEqual([log.loads, log.changed, log.closed, busy()], [current ? 1 : 0, current ? 0 : 1, ["#fix-sheet"], [false, false]], what);
  }
});

test("pinFix: the result is applied under the key the PUT used, not a key that changed meanwhile", async () => {
  const { s, srv, state, candidates, queue } = fixApp();
  const t = song("yt-a", "ka");
  state.queue.push(t);
  s.openFix(t);
  const pick = s.pinFix("pick");
  await flush();
  assert.deepEqual(srv.puts(), [["/api/match/ka", "pick"]]);
  candidates()[0].resolve({ key: "ka-other", candidates: [] }); // the same sheet's search answers with another key
  await flush();
  assert.equal(s.fix.key, "ka-other");
  srv.take("/api/match/ka", "PUT").resolve({ track: { id: "yt-a2" } });
  await pick;
  assert.deepEqual(queue(), [["playing"], ["yt-a2", "ka"]]);
});

test("pinFix: a normal pick sends one PUT, ignores a second tap meanwhile, then applies and closes", async () => {
  const { s, srv, log, state, busy, queue } = fixApp();
  const t = song("yt-a", "q:someone|song a");
  state.queue.push(t);
  s.openFix(t);
  const pick = s.pinFix("yt-right", { title: "Right title", artist: "Right artist" });
  s.pinFix("yt-other");
  await flush();
  assert.deepEqual(busy(), [true, true]);
  const put = srv.take("/api/match/q%3Asomeone%7Csong%20a", "PUT");
  assert.deepEqual(JSON.parse(put.body), { id: "yt-right", title: "Right title", artist: "Right artist" });
  assert.equal(srv.puts().length, 1);
  put.resolve({ track: { id: "yt-right", title: "Right title" } });
  await pick;
  assert.deepEqual(queue(), [["playing"], ["yt-right", "q:someone|song a"]]);
  assert.deepEqual([log.closed, busy(), log.loads, log.changed], [["#fix-sheet"], [false, false], 0, 1]);
});

test("pinFix: an API failure says why and frees the sheet; a retry works", async () => {
  const { s, srv, log, state, busy, queue } = fixApp();
  const t = song("yt-a", "ka");
  state.queue.push(t);
  s.openFix(t);
  const pick = s.pinFix("not-a-link");
  await flush();
  srv.take("/api/match/ka", "PUT").reject(fail(400, "That isn't a YouTube link"));
  await pick;
  assert.deepEqual([log.toasts, log.closed, queue(), busy()], [["That isn't a YouTube link"], [], [["playing"], ["yt-a", "ka"]], [false, false]]);
  const retry = s.pinFix("yt-right");
  await flush();
  srv.take("/api/match/ka", "PUT").resolve({ track: { id: "yt-right" } });
  await retry;
  assert.deepEqual([queue(), log.closed], [[["playing"], ["yt-right", "ka"]], ["#fix-sheet"]]);
});

test("pinFix: when the pending key lookup fails nothing is sent, and a later search lets it pin", async () => {
  const { s, srv, log, state, busy, queue } = fixApp();
  const t = ph("ph-1");
  state.queue.push(t);
  s.openFix(t);
  const pick = s.pinFix("yt-right");
  srv.take("/api/match/candidates", "POST").reject(fail(502));
  await pick;
  assert.deepEqual([srv.puts(), log.toasts, busy()], [[], ["Couldn't reach Reef"], [false, false]]);
  s.runFixSearch("song");
  srv.take("/api/match/candidates", "POST").resolve({ key: "k3", candidates: [] });
  await flush();
  const retry = s.pinFix("yt-right");
  await flush();
  srv.take("/api/match/k3", "PUT").resolve({ track: { id: "yt-right" } });
  await retry;
  assert.deepEqual(queue(), [["playing"], ["yt-right", "k3"]]);
});

// ---------- restore ----------

function restoreApp({ view = "home" } = {}) {
  const srv = server(), $ = dom(), log = { toasts: [], details: 0, lyrics: [], likes: 0, status: 0, loads: 0 };
  const playing = { id: "playing", title: "Song", channel: "Someone" };
  const audio = { src: "/api/stream/playing", currentTime: 42, paused: false, calls: 0 };
  audio.play = audio.pause = audio.load = () => { audio.calls++; };
  const state = { queue: [playing, { id: "next" }], index: 0, view, playlists: null, playlistsErr: null,
    history: [], historyLoaded: false, historyErr: null, home: null, homeErr: null, settings: {}, blocked: [], tiles: [] };
  const st = { restoreFile: { name: "backup.json" }, restoreMode: "replace", restoreBusy: false, restored: null };
  const lyr = { on: true, cache: new Map(), seq: 0, id: null, times: [], offset: 0 };
  const s = scope(["detail", "PAGE_API", "PAGE_TTL", "likedIds", "keptIds", "ndIds", "noteKept", "loadPage", "loadLikes", "loadPlaylists", "loadSettings",
    "loadHistory", "loadBlocklist", "loadHome", "fetchLyrics", "lyricsHints", "showLyrics", "resetLyrics", "restoreBackup"], {
    restoreGen: 0, blockGen: 0, homeAt: 0, homeTries: 0, homeTimer: undefined,
    api: srv.api, $, DC, M, st, state, audio, lyr, lyv: { dataset: {} }, lyricsPanel: { innerHTML: "" },
    confirm: () => true, FormData: class { append() {} }, rerender() {}, toast: (m) => log.toasts.push(m),
    loadBackupStatus: () => log.status++, paintLike: () => log.likes++, renderLibPlaylists() {}, renderLibSub() {},
    renderStatus() {}, renderHidden() {}, loadImportStatus() {}, releaseType: new Map(),
    renderDetail: () => log.details++, cur: () => state.queue[state.index], load: () => log.loads++,
    paintLyricsHead() {}, clearLyr() {}, renderLyrTools() {}, kickLyrics() {}, syncLyrics() {},
    renderLyrics: (d) => log.lyrics.push(d), lyrVisible: () => false, npOpen: () => true,
    RENDER: { home() {}, library() {} }, refreshKeepButtons() {}, updatePlayer() {},
    store: { set() {} }, renderHome() {},
  });
  const before = { queue: state.queue.slice(), index: 0, src: audio.src, time: audio.currentTime };
  const untouched = () => {
    assert.deepEqual([state.queue, state.index, audio.src, audio.currentTime, audio.paused, audio.calls, log.loads],
      [before.queue, before.index, before.src, before.time, false, 0, 0], "playback was touched");
    assert.ok(state.queue.every((t, i) => t === before.queue[i]));
  };
  // Runs restoreBackup to the point its POST succeeded.
  const restore = async () => {
    const p = s.restoreBackup();
    srv.take("/api/restore", "POST").resolve({ mode: "replace", sections: { likes: { after: 1 } } });
    await flush();
    return p;
  };
  const READS = ["/api/settings", "/api/history", "/api/blocklist", "/api/home", "/api/likes", "/api/playlists"];
  // Answers the newest (`latest`) or oldest read of each restore-refreshed path.
  const answer = (which, data) => { for (const path of READS) srv[which](path)[data[path] instanceof Error ? "reject" : "resolve"](data[path]); };
  return { s, srv, log, state, st, lyr, untouched, restore, answer, READS };
}
const NEW = {
  "/api/settings": { values: { crossfade: 4 } }, "/api/history": [{ id: "h-new" }], "/api/blocklist": [{ name: "New" }],
  "/api/home": { shelves: [{ kind: "tracks", items: [{ id: "home-new" }] }] }, "/api/likes": [{ id: "liked-new" }],
  "/api/playlists": [{ id: "p-new", name: "Restored" }],
};
const OLD = {
  "/api/settings": { values: { crossfade: 0 } }, "/api/history": [{ id: "h-old" }], "/api/blocklist": [{ name: "Old" }],
  "/api/home": { shelves: [{ kind: "tracks", items: [{ id: "home-old" }] }] }, "/api/likes": [{ id: "liked-old" }],
  "/api/playlists": [{ id: "p-old", name: "Before" }],
};
const shown = (app) => ({
  settings: app.state.settings, history: app.state.history, blocked: app.state.blocked, home: app.state.home?.shelves,
  likes: [...app.s.likedIds], playlists: app.state.playlists,
});
const expectNew = { settings: NEW["/api/settings"].values, history: NEW["/api/history"], blocked: NEW["/api/blocklist"],
  home: NEW["/api/home"].shelves, likes: ["liked-new"], playlists: NEW["/api/playlists"] };

test("restoreBackup: a success reloads likes and playlists with settings, history, blocklist and Home", async () => {
  const app = restoreApp({ view: "library" });
  app.s.likedIds.add("liked-before");
  await app.restore();
  for (const path of app.READS) assert.equal(app.srv.count(path), 1, path);
  app.answer("take", NEW);
  await flush();
  assert.deepEqual(shown(app), expectNew);
  assert.ok(app.log.likes >= 1, "the Like button repainted");
  assert.deepEqual([app.st.restoreBusy, app.st.restoreFile, app.log.toasts, app.log.status], [false, null, ["Backup restored"], 1]);
  app.untouched();
});

test("restoreBackup: reads asked before the restore can't overwrite or fail what it reloaded", async () => {
  for (const oldOutcome of ["success", "failure"]) {
    const app = restoreApp({ view: "library" });
    for (const f of ["loadSettings", "loadHistory", "loadBlocklist", "loadHome", "loadLikes", "loadPlaylists"]) app.s[f]();
    await app.restore();
    for (const path of app.READS) assert.equal(app.srv.count(path), 2, path);
    app.answer("latest", NEW); // the restore's own reads finish first
    await flush();
    app.answer("take", oldOutcome === "success" ? OLD : Object.fromEntries(app.READS.map((p) => [p, fail(500)])));
    await flush();
    assert.deepEqual(shown(app), expectNew, oldOutcome);
    assert.deepEqual([app.state.playlistsErr, app.state.historyErr, app.state.homeErr], [null, null, null], oldOutcome);
    app.untouched();
  }
});

test("restoreBackup: cached Reef playlist pages go; the one shown reloads; other pages stay", async () => {
  const app = restoreApp({ view: "detail" });
  const { detail } = app.s, old = { name: "Before", tracks: [{ id: "a", title: "A" }] }, album = { title: "Album" };
  Object.assign(detail, { kind: "local", id: "p1", key: "local/p1" });
  detail.cache.set("local/p1", { at: Date.now(), data: old });
  detail.cache.set("local/p2", { at: Date.now(), data: { name: "Other" } });
  detail.cache.set("album/x", { at: Date.now(), data: album });
  detail.errors.set("local/p3", fail(500));
  await app.restore();
  assert.deepEqual([...detail.cache.keys()], ["album/x"]);
  assert.deepEqual([...detail.errors.keys()], []);
  assert.equal(app.srv.count("/api/playlists/p1"), 1, "the shown playlist reloads");
  assert.equal(app.srv.count("/api/playlists/p2"), 0, "a playlist not shown is only dropped");
  const fresh = { name: "Restored", tracks: [{ id: "b", title: "B" }] };
  app.srv.take("/api/playlists/p1").resolve(fresh);
  await flush();
  assert.deepEqual(detail.cache.get("local/p1").data, fresh);
  assert.equal(app.log.details, 1);
  app.untouched();
});

test("loadPage: a playlist load asked before a restore can't repopulate or unlock the cache", async () => {
  for (const oldOutcome of ["success", "failure"]) {
    const app = restoreApp({ view: "detail" });
    const { detail } = app.s;
    Object.assign(detail, { kind: "local", id: "p1", key: "local/p1" });
    const stale = app.s.loadPage("local/p1", { force: true });
    await app.restore();
    const fresh = detail.inflight.get("local/p1");
    assert.ok(fresh && fresh !== stale, "the restore's reload is its own request");
    const [oldReq, newReq] = app.srv.calls.filter((c) => c.path === "/api/playlists/p1");
    if (oldOutcome === "failure") {
      oldReq.reject(fail(500)); // fails while the new load is still running
      await flush();
      assert.equal(detail.inflight.get("local/p1"), fresh, "the newer request stays in flight");
      assert.equal(detail.errors.has("local/p1"), false);
      newReq.resolve({ name: "Restored", tracks: [] });
    } else {
      newReq.resolve({ name: "Restored", tracks: [] });
      await flush();
      oldReq.resolve({ name: "Before", tracks: [] }); // answers after the new one
    }
    await Promise.all([stale, fresh]);
    await flush();
    assert.equal(detail.cache.get("local/p1").data.name, "Restored", oldOutcome);
    assert.equal(detail.errors.has("local/p1"), false);
    assert.equal(detail.inflight.has("local/p1"), false);
    assert.equal(app.srv.count("/api/playlists/p1"), 2, `${oldOutcome}: no extra request`);
    app.untouched();
  }
});

test("loadPage: a catalog page asked before a restore keeps its data but can't bring back old likes", async () => {
  const app = restoreApp();
  const { detail } = app.s;
  app.s.likedIds.add("liked-old");
  const stale = app.s.loadPage("album/x");
  await app.restore();
  app.answer("take", NEW); // the restored likes: only liked-new
  await flush();
  assert.deepEqual([...app.s.likedIds], ["liked-new"]);
  const album = { title: "Album", tracks: [{ id: "liked-new", liked: false }, { id: "liked-old", liked: true }] };
  app.srv.take("/api/album/x").resolve(album); // the pre-restore page answers last, with pre-restore flags
  await stale;
  await flush();
  assert.deepEqual([...app.s.likedIds], ["liked-new"]);
  assert.deepEqual(detail.cache.get("album/x").data, album, "the catalog page itself is still cached");
  assert.equal(app.srv.count("/api/album/x"), 1, "no catalog reload");
  // A page asked after the restore still updates likes from its flags.
  const fresh = app.s.loadPage("album/y");
  app.srv.take("/api/album/y").resolve({ title: "Other", tracks: [{ id: "liked-y", liked: true }] });
  await fresh;
  assert.deepEqual([...app.s.likedIds].sort(), ["liked-new", "liked-y"]);
  app.untouched();
});

// The shipped search readers with the real noteKept; `restored()` stands in for restoreBackup's restoreGen bump and
// the restored likes / kept state it loads.
function searchApp() {
  const srv = server(), log = { renders: 0 };
  const state = { view: "search", query: "", link: null, searching: false, searchError: null, filter: "top", all: null,
    results: [], kinds: new Map() };
  const s = scope(["likedIds", "keptIds", "ndIds", "noteKept", "grouped", "searchSeq", "fetchSearch", "loadKind",
    "refetchSearch", "runSearch"], {
    restoreGen: 0, state, api: srv.api, DC, $: dom(), cur: () => null, paintLike() {}, renderSearch: () => log.renders++,
    currentSource: () => "yt", looksLikeLink: () => false, LINK_RE: /^$/, rememberSearch() {}, sug: { timer: 0, seq: 0 },
    location: { hash: "#search" },
  });
  const restored = () => {
    s.restoreGen++;
    s.likedIds.clear(); s.likedIds.add("restored-song");
    s.keptIds.clear(); s.keptIds.add("kept-song");
  };
  // What a pre-restore answer says about the same songs.
  const stale = [{ id: "restored-song", liked: false, kept: false }, { id: "kept-song", liked: true, kept: false }];
  const meta = () => [[...s.likedIds].sort(), [...s.keptIds].sort()];
  return { s, srv, log, state, restored, stale, meta };
}
const RESTORED = [["restored-song"], ["kept-song"]];

test("runSearch: results asked before a restore still show but can't overwrite restored likes / kept", async () => {
  const { s, srv, log, state, restored, stale, meta } = searchApp();
  const run = s.runSearch("scratch");
  restored();
  srv.take("/api/search/all?q=scratch").resolve({ songs: stale, videos: [{ id: "video-1", liked: true }] });
  await run;
  assert.deepEqual(meta(), RESTORED);
  assert.deepEqual([state.results, state.all.videos.length, state.searching, state.searchError], [stale, 1, false, null]);
  assert.ok(log.renders > 0);
  // A search asked after the restore updates them as usual.
  const again = s.runSearch("again");
  srv.take("/api/search/all?q=again").resolve({ songs: [{ id: "restored-song", liked: false }, { id: "new-song", kept: true }], videos: [] });
  await again;
  assert.deepEqual(meta(), [[], ["kept-song", "new-song"]]);
});

test("loadKind: a chip list asked before a restore is stored but can't overwrite restored likes / kept", async () => {
  const { s, srv, log, state, restored, stale, meta } = searchApp();
  Object.assign(state, { query: "scratch", all: { songs: [] }, filter: "songs" });
  const load = s.loadKind("songs");
  restored();
  srv.take("/api/search?q=scratch&src=yt").resolve(stale);
  await load;
  assert.deepEqual(meta(), RESTORED);
  assert.deepEqual(state.kinds.get("songs"), { items: stale });
  assert.equal(log.renders, 1);
  const videos = s.loadKind("videos");
  srv.take("/api/search?q=scratch&filter=videos").resolve([{ id: "restored-song", liked: false }]);
  await videos;
  assert.deepEqual(meta(), [[], ["kept-song"]]);
});

test("refetchSearch: a refetch asked before a restore still updates results but not restored likes / kept", async () => {
  const { s, srv, state, restored, stale, meta } = searchApp();
  state.query = "scratch";
  const refetch = s.refetchSearch();
  restored();
  srv.take("/api/search/all?q=scratch").resolve({ songs: stale, videos: [] });
  await refetch;
  assert.deepEqual(meta(), RESTORED);
  assert.deepEqual(state.results, stale);
  const again = s.refetchSearch();
  srv.take("/api/search/all?q=scratch").resolve({ songs: [{ id: "restored-song", liked: false }], videos: [] });
  await again;
  assert.deepEqual(meta(), [[], ["kept-song"]]);
});

test("restoreBackup: shown lyrics reload; an older lyrics read can't render or evict the new one", async () => {
  for (const oldOutcome of ["success", "failure"]) {
    const app = restoreApp();
    const lyrics = (p) => p.startsWith("/api/lyrics/playing?");
    app.s.showLyrics();
    assert.equal(app.lyr.id, "playing");
    await app.restore();
    const fresh = app.lyr.cache.get("playing");
    assert.ok(fresh, "lyrics asked again");
    const [oldReq, newReq] = app.srv.calls.filter((c) => lyrics(c.path));
    assert.ok(newReq, "a second lyrics request");
    if (oldOutcome === "failure") oldReq.reject(fail(500)); else oldReq.resolve({ plain: "old words" });
    await flush();
    assert.equal(app.lyr.cache.get("playing"), fresh, `${oldOutcome}: the new lyrics stay cached`);
    newReq.resolve({ plain: "new words" });
    await flush();
    assert.deepEqual(app.log.lyrics, [{ plain: "new words" }], oldOutcome);
    app.untouched();
  }
});

test("fetchLyrics: a failed read is dropped so it can be asked again; a 404 sticks", async () => {
  const app = restoreApp();
  const t = { id: "x", title: "X" };
  const p = app.s.fetchLyrics(t);
  app.srv.take((path) => path.startsWith("/api/lyrics/x?")).reject(fail(500));
  assert.ok((await p).error);
  assert.equal(app.lyr.cache.has("x"), false);
  const q = app.s.fetchLyrics(t);
  app.srv.take((path) => path.startsWith("/api/lyrics/x?")).reject(fail(404));
  assert.equal(await q, null);
  assert.equal(app.lyr.cache.get("x"), q);
});

test("restoreBackup: a failed restore reloads nothing and keeps the caches", async () => {
  const app = restoreApp({ view: "detail" });
  const { detail } = app.s;
  detail.cache.set("local/p1", { at: Date.now(), data: { name: "Before" } });
  const kept = Promise.resolve({ plain: "words" });
  app.lyr.cache.set("playing", kept);
  const p = app.s.restoreBackup();
  app.srv.take("/api/restore", "POST").reject(fail(400, "Not a Reef backup"));
  await p;
  assert.deepEqual(app.log.toasts, ["Couldn't restore: Not a Reef backup"]);
  assert.deepEqual(app.srv.calls.filter((c) => c.method === "GET"), []);
  assert.deepEqual([detail.cache.has("local/p1"), app.lyr.cache.get("playing"), app.st.restoreBusy, app.log.status], [true, kept, false, 1]);
  app.untouched();
});
