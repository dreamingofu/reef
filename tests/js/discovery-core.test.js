"use strict";
// discovery-core (DC): search groups, page sort / filter, Home order, thumbnails, recents, hidden artists.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { load, snapshot } = require("./harness");

const DC = load("discovery-core");

// node check for the discovery-core section of static/app.js (sliced between its sentinels).

const tr = (id, title = id, extra = {}) => ({ id, title, channel: "A", ...extra });

test("searchGroups: plan order, top result left out of its group, previews + more", () => {
  const all = {
    top: { type: "artist", id: "UC1", title: "Daft Punk" },
    songs: [tr("s1"), tr("s2"), tr("s3"), tr("s4"), tr("s5")],
    videos: [tr("v1")], albums: [], playlists: [{ type: "playlist", id: "PL1" }],
    artists: [{ type: "artist", id: "UC1" }, { type: "artist", id: "UC2" }],
  };
  const g = DC.searchGroups(all);
  assert.deepEqual(g.map((x) => x.kind), ["songs", "artists", "videos", "playlists"]);
  assert.deepEqual(g[0].items.map((x) => x.id), ["s1", "s2", "s3", "s4"]);
  assert.equal(g[0].more, true); assert.equal(g[0].total, 5);
  assert.deepEqual(g[1].items.map((x) => x.id), ["UC2"]); // the top artist isn't listed twice
  assert.equal(g[1].more, false);
  const t = DC.searchGroups({ top: { type: "track", track: tr("s1") }, songs: [tr("s1"), tr("s2")] });
  assert.deepEqual(t[0].items.map((x) => x.id), ["s2"]);
  assert.deepEqual(DC.searchGroups(null), []);
  assert.deepEqual(DC.searchGroups({ songs: [null, { title: "no id" }] }), []);
});

test("topId / chips", () => {
  assert.equal(DC.topId({ type: "track", track: { id: "x" } }), "x");
  assert.equal(DC.topId({ type: "album", id: "MPRE1" }), "MPRE1");
  assert.equal(DC.topId(null), null);
  assert.deepEqual(DC.chips(true), ["top", "songs", "videos", "albums", "artists", "playlists", "kept"]);
  assert.deepEqual(DC.chips(false), ["top", "songs", "kept"]);
});

test("viewTracks: filter by words (accents folded), sort stable by name / artist", () => {
  const ts = [tr("1", "Échelon", { artists: [{ name: "Zed" }] }), tr("2", "alpha", { channel: "Bob" }),
    tr("3", "Beta", { artists: [{ name: "Ann" }], album: "Live" }), tr("4", "alpha", { channel: "Abe" })];
  assert.deepEqual(DC.viewTracks(ts).map((r) => r.i), [0, 1, 2, 3]);
  assert.deepEqual(DC.viewTracks(ts, { sort: "name" }).map((r) => r.i), [1, 3, 2, 0]); // alpha(2) before alpha(4): stable
  assert.deepEqual(DC.viewTracks(ts, { sort: "artist" }).map((r) => r.t.id), ["4", "3", "2", "1"]);
  assert.deepEqual(DC.viewTracks(ts, { q: "echelon" }).map((r) => r.i), [0]);
  assert.deepEqual(DC.viewTracks(ts, { q: "live ann" }).map((r) => r.i), [2]); // album + artist words, any order
  assert.deepEqual(DC.viewTracks(ts, { q: "  " }).length, 4);
  assert.deepEqual(DC.viewTracks(null), []);
  assert.equal(DC.sortOf("name"), "name"); assert.equal(DC.sortOf("bogus"), "default");
});

test("homePlan: moods out, quick first, server order kept, local jump back inserted", () => {
  const shelves = [{ id: "quick", kind: "tracks", items: [1] }, { id: "on_repeat", kind: "tracks", items: [1] },
    { id: "mixed", kind: "seeds", items: [1] }, { id: "liked", kind: "tracks", items: [1] },
    { id: "ytm:x", kind: "cards", items: [1] }, { id: "charts", kind: "tracks", items: [1] },
    { id: "empty", kind: "tracks", items: [] }, { id: "moods", kind: "moods", items: [{ label: "Chill" }] }];
  const p = DC.homePlan(shelves, { recent: 3 });
  assert.equal(p.quick, "quick");
  assert.deepEqual(p.moods, [{ label: "Chill" }]);
  assert.deepEqual(p.order.map((s) => s.id), ["on_repeat", "mixed", "jump_back", "liked", "ytm:x", "charts"]);
  assert.equal(p.order[2].local, true);
  assert.deepEqual(DC.homePlan(shelves, { recent: 0 }).order.map((s) => s.id), ["on_repeat", "mixed", "liked", "ytm:x", "charts"]);
  const q = DC.homePlan([{ id: "jump_back", kind: "tracks", items: [1] }], { recent: 5 });
  assert.deepEqual(q.order.map((s) => [s.id, s.local]), [["jump_back", false]]); // the server's wins
  assert.deepEqual(DC.homePlan(null, { recent: 2 }).order.map((s) => s.id), ["jump_back"]);
});

test("cookie: a closed path inside the box with 9 lobes", () => {
  const p = DC.cookiePath(9, 0.06, 144);
  assert.match(p, /^M[\d.]+,[\d.]+L.*Z$/);
  const nums = p.slice(1, -1).split(/[L ]/).map((s) => s.split(",").map(Number));
  assert.equal(nums.length, 144);
  const r = nums.map(([x, y]) => Math.hypot(x - 50, y - 50));
  assert.ok(Math.max(...r) <= 50.01 && Math.min(...r) >= 50 * (1 - 0.12) - 0.01);
  let peaks = 0;
  for (let k = 0; k < r.length; k++) if (r[k] > r[(k + r.length - 1) % r.length] && r[k] >= r[(k + 1) % r.length]) peaks++;
  assert.equal(peaks, 9);
  assert.match(DC.cookieMask(), /^url\("data:image\/svg\+xml,%3Csvg/);
});

test("thumbSrc: only the hosts /api/thumb takes", () => {
  assert.equal(DC.thumbSrc("https://lh3.googleusercontent.com/a=w544-h544-l90-rj"),
    "/api/thumb?u=" + encodeURIComponent("https://lh3.googleusercontent.com/a=w544-h544-l90-rj") + "&size=64");
  assert.ok(DC.thumbSrc("https://i.ytimg.com/vi/x/hqdefault.jpg?sqp=1", 96).endsWith("&size=96"));
  for (const bad of ["http://i.ytimg.com/x", "https://evil.example/x", "https://lh3.googleusercontent.com.evil.example/x",
    "https://lh3.googleusercontent.com/x?y=1", "https://i.ytimg.com:444/x", "https://u:p@i.ytimg.com/x", "", null, "nonsense"]) {
    assert.equal(DC.thumbSrc(bad), null, String(bad));
  }
});

test("resized: Google art at another size, crop flag kept; others untouched", () => {
  assert.equal(DC.resized("https://lh3.googleusercontent.com/a=w544-h544-p-l90-rj", 900), "https://lh3.googleusercontent.com/a=w900-h900-p-l90-rj");
  assert.equal(DC.resized("https://i.ytimg.com/vi/x/mqdefault.jpg", 900), "https://i.ytimg.com/vi/x/mqdefault.jpg");
  assert.equal(DC.resized(null, 900), null);
});

test("header progress + parallax", () => {
  assert.equal(DC.headerProgress(-20, 300), 0);
  assert.equal(DC.headerProgress(150, 300), 0.5);
  assert.equal(DC.headerProgress(900, 300), 1);
  assert.equal(DC.headerProgress(10, 0), 0);
  assert.equal(DC.parallax(-10, 400), 0);
  assert.equal(DC.parallax(100, 400), 40);
  assert.equal(DC.parallax(1000, 400), 160);
});

test("collectionLines", () => {
  assert.deepEqual(DC.collectionLines({ type: "Album", year: "2016", count: 14, seconds: 3000 }), { eyebrow: "Album · 2016", meta: "14 songs · 50 min" });
  assert.deepEqual(DC.collectionLines({ type: "Playlist", count: 1, seconds: 4000 }), { eyebrow: "Playlist", meta: "1 song · 1 hr 7 min" });
  assert.deepEqual(DC.collectionLines({}), { eyebrow: "", meta: "" });
});

test("dropRecent / putBack", () => {
  const r = DC.dropRecent(["a", "Daft Punk", "c"], "daft punk");
  assert.deepEqual(r, { list: ["a", "c"], at: 1 });
  assert.deepEqual(DC.putBack(r.list, "Daft Punk", r.at), ["a", "Daft Punk", "c"]);
  assert.deepEqual(DC.putBack(["x"], "y", 9), ["x", "y"]);
  assert.deepEqual(DC.dropRecent(["a"], "z"), { list: ["a"], at: -1 });
});

test("keepProgress", () => {
  assert.deepEqual(DC.keepProgress([]), { state: "", done: 0, total: 0, frac: 0 });
  assert.deepEqual(DC.keepProgress(["kept", "none", "x"]), { state: "none", done: 1, total: 2, frac: 0.5 });
  assert.equal(DC.keepProgress(["kept", "pending"]).state, "pending");
  assert.equal(DC.keepProgress(["kept", "kept"]).state, "kept");
});

test("artistLine: the search's top artist count is the monthly audience, never 'subscribers'", () => {
  assert.equal(DC.artistLine({ audience: "68.7M" }), "68.7M monthly audience");
  assert.equal(DC.artistLine({ monthly_listeners: "68.7M", subscribers: "7.2M" }), "68.7M monthly audience · 7.2M subscribers");
  assert.equal(DC.artistLine({ subscribers: "7.2M" }), "7.2M subscribers");
  assert.equal(DC.artistLine({}), ""); assert.equal(DC.artistLine(null), "");
});

test("keptPool: kept songs across the preview, the full songs search and videos, once each, in order", () => {
  const kept = new Set(["b", "d", "v"]);
  const preview = [tr("a"), tr("b")], full = [tr("b"), tr("c"), tr("d")], vids = [tr("v")];
  assert.deepEqual(DC.keptPool([preview, full, vids], (t) => kept.has(t.id)).map((t) => t.id), ["b", "d", "v"]);
  assert.deepEqual(DC.keptPool([preview, undefined, null], (t) => kept.has(t.id)).map((t) => t.id), ["b"]); // songs not loaded yet
  assert.deepEqual(DC.keptPool(null, () => true), []);
});

test("kindAction: a failed chip list waits for Retry instead of re-asking on every paint", () => {
  assert.equal(DC.kindAction(undefined), "fetch");
  assert.equal(DC.kindAction({ loading: true }), "wait");
  assert.equal(DC.kindAction({ items: [] }), "show");
  assert.equal(DC.kindAction({ err: { status: 502 } }), "show"); // the error state shows; Retry deletes the entry -> "fetch"
});

test("clearedSearch: ✕ leaves nothing searching (a pending answer is dropped as stale, so nothing else resets it)", () => {
  const st = { query: "pending query", searching: true, searchError: { status: 502 }, results: [1], all: { top: null }, link: { kind: "" }, filter: "albums", view: "search" };
  Object.assign(st, DC.clearedSearch());
  assert.deepEqual(st, { query: "", searching: false, searchError: null, results: [], all: null, link: null, filter: "top", view: "search" });
  assert.notEqual(DC.clearedSearch().results, DC.clearedSearch().results); // a fresh list each time
});

test("blockAdd / blockRemove: a confirmed hide / unhide is this device's list at once", () => {
  const A = { name: "Hidden Artist", id: "UCRr1xG_2WIDs18a6cIiCxeA" };
  assert.deepEqual(DC.blockAdd(null, A), [{ name: "Hidden Artist", id: A.id, at: null }]); // never loaded: just it
  const list = [{ name: "Daft Punk - Topic", id: null, at: 1 }];
  assert.deepEqual(DC.blockAdd(list, { name: "daft punk", id: "UCx" }), [{ name: "Daft Punk - Topic", id: "UCx", at: 1 }]); // same artist: id filled in
  assert.equal(DC.blockAdd(list, A).length, 2);
  assert.deepEqual(DC.blockAdd(list, { id: "UCy" }), list); // no name: nothing to add
  assert.notEqual(DC.blockAdd(list, A), list); // a new list
  assert.deepEqual(DC.blockRemove([...list, { name: "Hidden Artist", id: A.id }], [{ id: A.id, name: "Other name" }]), list); // by id
  assert.deepEqual(DC.blockRemove(list, [{ name: "DAFT PUNK VEVO" }]), []); // by name key
  assert.deepEqual(DC.blockRemove(null, [A]), []);
  assert.equal(DC.blockKey("Beyoncé - Topic"), DC.blockKey("beyoncé"));
  assert.equal(DC.sameArtist({ name: "A", id: "UC1" }, { name: "B", id: "UC1" }), true);
  assert.equal(DC.sameArtist({ name: "A" }, { name: "B" }), false);
});

test("playableCopy / genCurrent: an invalidated page whose refresh failed is never played; old blocklist answers are dropped", () => {
  const T = 300000, now = 1e6;
  assert.equal(DC.playableCopy(undefined, now, T), "none");
  assert.equal(DC.playableCopy({ at: now - 1000 }, now, T), "fresh");
  assert.equal(DC.playableCopy({ at: now - T }, now, T), "stale"); // only expired: the old copy minus hidden artists
  assert.equal(DC.playableCopy({ at: 0, inv: true, err: null }, now, T), "stale"); // invalidated, refresh not failed (yet)
  assert.equal(DC.playableCopy({ at: 0, inv: true, err: { status: 502 } }, now, T), "refuse");
  assert.equal(DC.playableCopy({ at: now, data: {} }, now, T), "fresh"); // a successful refresh replaces the entry (no inv)
  assert.equal(DC.genCurrent(3, 3), true);
  assert.equal(DC.genCurrent(3, 4), false); // a hide / unhide happened after the GET was asked
});

// ---- constants, text folding, stable sorts, inputs never mutated ----
test("constants: the §10.1 group order and labels, previews, sorts, the filter threshold", () => {
  assert.deepStrictEqual(DC.GROUPS, ["songs", "artists", "albums", "videos", "playlists"]);
  for (const g of DC.GROUPS) assert.equal(typeof DC.LABEL[g], "string");
  assert.deepStrictEqual(DC.PREVIEW, { songs: 4, artists: 3, albums: 3, videos: 3, playlists: 3 });
  assert.deepStrictEqual(DC.SORTS, ["default", "name", "artist"]);
  assert.deepStrictEqual(Object.keys(DC.SORT_LABEL), DC.SORTS);
  assert.equal(DC.FILTER_MIN, 12);
  const g = DC.searchGroups({ artists: Array.from({ length: 10 }, (_, i) => ({ id: "UC" + i })) });
  assert.deepStrictEqual([g[0].items.length, g[0].total, g[0].more], [DC.PREVIEW.artists, 10, true]);
  assert.equal(DC.sortOf("bogus"), "default");
  assert.equal(DC.sortOf("artist"), "artist");
});

test("fold and artistOf: what sorting and filtering compare", () => {
  assert.equal(DC.fold("  Éclair  DE   Ça "), "eclair de ca");
  assert.equal(DC.fold("ﬁn"), "fin"); // compatibility forms fold too
  assert.equal(DC.fold(null), "");
  assert.equal(DC.fold(0), "0");
  assert.equal(DC.artistOf({ artists: [{ name: "" }, { name: "B" }], channel: "C" }), "B");
  assert.equal(DC.artistOf({ artists: [], channel: "C", artist: "D" }), "C");
  assert.equal(DC.artistOf({ artist: "D" }), "D");
  assert.equal(DC.artistOf(null), "");
  assert.deepStrictEqual(DC.viewTracks([{ id: "1", title: "Café", channel: "X" }, { id: "2", title: "Tea" }], { q: "CAFE x" }).map((r) => r.i), [0]);
});

test("viewTracks: name / artist sorts are stable for equal keys and keep the original index", () => {
  const tracks = [{ id: "1", title: "b" }, { id: "2", title: "A" }, null, { id: "3", title: "a" }, { id: "4", title: "B" }];
  assert.deepStrictEqual(DC.viewTracks(tracks, { sort: "name" }).map((r) => r.i), [1, 3, 0, 4]);
  assert.deepStrictEqual(DC.viewTracks(tracks).map((r) => r.i), [0, 1, 3, 4]); // default: page order, nulls left out
  assert.deepStrictEqual(DC.viewTracks(tracks, { sort: "bogus" }).map((r) => r.i), [0, 1, 3, 4]);
  const byArtist = [{ title: "z", channel: "B" }, { title: "y", channel: "a" }, { title: "x", channel: "b" }];
  assert.deepStrictEqual(DC.viewTracks(byArtist, { sort: "artist" }).map((r) => r.i), [1, 2, 0]); // artist, then title
  assert.deepStrictEqual(DC.viewTracks(null), []);
});

test("no discovery function mutates its inputs", () => {
  const all = { top: { type: "track", track: { id: "s1" } }, songs: [{ id: "s1" }, { id: "s2" }], artists: [{ id: "UC1" }] };
  const tracks = [{ id: "b", title: "B" }, { id: "a", title: "a" }, null];
  const shelves = [{ id: "quick", items: [1] }, { id: "mixed", items: [1] }, { id: "m", kind: "moods", items: ["x"] }];
  const recents = ["One", "two"], blocked = [{ name: "X", id: null, at: 1 }], lists = [[{ id: "k" }], [{ id: "k" }, { id: "j" }]];
  const s = snapshot(all, tracks, shelves, recents, blocked, lists);
  DC.searchGroups(all); DC.viewTracks(tracks, { sort: "name", q: "a" }); DC.viewTracks(tracks, { sort: "artist" });
  DC.homePlan(shelves, { recent: 3 }); DC.dropRecent(recents, "ONE"); DC.putBack(recents, "three", 1);
  DC.blockAdd(blocked, { name: "x", id: "UC9" }); DC.blockAdd(blocked, { name: "Y" }); DC.blockRemove(blocked, [{ name: "x" }]);
  DC.keptPool(lists, () => true); DC.keepProgress(["kept", "none"]);
  s.same(assert);
  assert.notEqual(DC.dropRecent(recents, "zzz").list, recents);
  assert.notEqual(DC.putBack(recents, "One", 0), recents);
  assert.notEqual(DC.blockAdd(blocked, null), blocked);
});
