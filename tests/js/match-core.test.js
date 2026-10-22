"use strict";
// match-core (M): placeholders, replacement swaps, SponsorBlock segment skips.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { load, snapshot } = require("./harness");

const M = load("match-core");
const Q = load("queue-core");

// Node checks for the match-core section of static/app.js (placeholders, replacement swap, SponsorBlock skips).

const t = (id, extra = {}) => ({ id, title: id, channel: "c", ...extra });
const ph = (id, extra = {}) => M.placeholder({ title: "Song " + id, artist: "Art", duration: 200, ...extra }, "pq:" + id);

test("placeholder shape", () => {
  const p = ph("1", { key: "imp:1", album: "Alb" });
  assert.deepEqual(p, { id: "pq:1", q: true, title: "Song 1", artist: "Art", channel: "Art", duration: 200, album: "Alb", key: "imp:1" });
  assert.equal(M.isPlaceholder(p), true);
  assert.equal(M.isPlaceholder(t("a")), false);
  assert.equal(M.placeholder({ title: "x", duration: "nope" }, "pq:2").duration, null);
});

test("resolution window: current + next 2 only", () => {
  const q = [ph("0"), t("a"), ph("1"), ph("2"), ph("3"), t("b"), ph("4")];
  assert.deepEqual(M.toResolve(q, 0), [0, 2]);      // index 0..2
  assert.deepEqual(M.toResolve(q, 1), [2, 3]);      // index 1..3
  assert.deepEqual(M.toResolve(q, 3), [3, 4]);      // 3..5
  assert.deepEqual(M.toResolve(q, 5), [6]);         // 5..7 (only 6 exists)
  assert.deepEqual(M.toResolve(q, 6), [6]);
  assert.deepEqual(M.toResolve(q, -1), [0]);        // nothing loaded yet: -1..1 -> 0
  assert.deepEqual(M.toResolve([], 0), []);
  assert.deepEqual(M.toResolve(q, 1, 0), []);       // window 0: the current one only (a track)
});

test("swap in place, keeps auto flag, index unchanged", () => {
  const q = [t("a"), ph("1"), { ...ph("2"), auto: true }, t("b")];
  const r = M.swap(q, 0, "pq:1", t("x"));
  assert.deepEqual(r.queue.map((x) => x.id), ["a", "x", "pq:2", "b"]);
  assert.equal(r.index, 0); assert.equal(r.at, 1); assert.equal(r.dropped, false);
  assert.equal(q[1].id, "pq:1"); // input untouched
  const r2 = M.swap(r.queue, 0, "pq:2", t("y"));
  assert.equal(r2.queue[2].auto, true);
  assert.equal(r2.queue[2].id, "y");
});

test("swap of the current entry (dead track replacement)", () => {
  const q = [t("a"), t("dead"), t("b")];
  const r = M.swap(q, 1, "dead", { ...t("live"), replaced_from: "dead" });
  assert.deepEqual(r.queue.map((x) => x.id), ["a", "live", "b"]);
  assert.equal(r.index, 1);
  assert.equal(r.queue[1].replaced_from, "dead");
});

test("swap drops instead of duplicating an id", () => {
  const q = [t("a"), ph("1"), t("x"), t("b")];
  let r = M.swap(q, 0, "pq:1", t("x"));
  assert.deepEqual(r.queue.map((x) => x.id), ["a", "x", "b"]);
  assert.equal(r.dropped, true); assert.equal(r.index, 0);
  r = M.swap(q, 1, "pq:1", t("x")); // it was current: the next takes its place
  assert.equal(r.index, 1); assert.equal(r.queue[r.index].id, "x");
  r = M.swap(q, 3, "pq:1", t("x")); // before the current: current shifts down
  assert.equal(r.index, 2); assert.equal(r.queue[r.index].id, "b");
  r = M.swap(q, 0, "missing", t("z"));
  assert.equal(r.at, -1); assert.deepEqual(r.queue, q);
  // ids stay unique, so queue-core keeps working on the result
  const u = Q.unshuffle(r.queue, 0, r.queue);
  assert.equal(u.queue.length, q.length);
});

test("canReplace", () => {
  for (const b of ["GONE", "REGION", "MEMBERS_OR_PAID"]) assert.equal(M.canReplace(t("dQw4w9WgXcQ"), b), true);
  assert.equal(M.canReplace(t("mc:123"), "GONE"), true);
  for (const b of ["LOGIN_REQUIRED", "BOT_WALL", "NETWORK", "UNKNOWN", null]) assert.equal(M.canReplace(t("dQw4w9WgXcQ"), b), false);
  assert.equal(M.canReplace(t("nd:abc"), "GONE"), false);           // library tracks: no
  assert.equal(M.canReplace(ph("1"), "GONE"), false);
  assert.equal(M.canReplace({ ...t("dQw4w9WgXcQ"), replaced_from: "x" }, "GONE"), false); // never a chain
  assert.equal(M.canReplace(t("dQw4w9WgXcQ"), "GONE", { enabled: false }), false);
  assert.equal(M.canReplace(null, "GONE"), false);
});

test("segment skip-once", () => {
  const segs = M.cleanSegments([{ start: 249.38, end: 281.52 }, { start: 0, end: 21.8 }, { start: 5, end: 3 }, { start: "x", end: 9 }, null]);
  assert.deepEqual(segs, [{ start: 0, end: 21.8 }, { start: 249.38, end: 281.52 }]);
  const done = new Set();
  assert.equal(M.segmentAt(segs, done, 0), 0);        // first timeupdate at 0
  assert.equal(M.segmentAt(segs, done, 0.3), 0);
  assert.equal(M.segmentAt(segs, done, 21.5), -1);    // in the last half second: let it play out
  assert.equal(M.segmentAt(segs, done, 100), -1);
  done.add(0);
  assert.equal(M.segmentAt(segs, done, 3), -1);       // skipped once: never again
  assert.equal(M.segmentAt(segs, done, 250), 1);
  assert.equal(M.segmentTarget(segs[0], 281.5), 21.8);
  assert.equal(M.segmentTarget(segs[1], 281.5), 281.25); // kept inside the element's length
  assert.equal(M.segmentTarget(segs[1], 0), 281.52);     // length unknown
});

test("a deliberate seek into a segment plays it", () => {
  const segs = [{ start: 0, end: 21.8 }, { start: 249.38, end: 281.52 }];
  const done = new Set([0]);
  for (const i of M.entered(segs, 260)) done.add(i); // the user seeks into the outro
  assert.equal(M.segmentAt(segs, done, 260.3), -1);
  assert.deepEqual(M.entered(segs, 100), []);
  assert.deepEqual(M.entered(segs, 0), [0]);
});

// ---- constants, boundaries, inputs never mutated ----
test("the lookup window and the replaceable blocks", () => {
  assert.equal(M.WINDOW, 2);
  assert.deepStrictEqual(M.REPLACE_BLOCKS, ["GONE", "REGION", "MEMBERS_OR_PAID"]);
  const q = [ph("0"), ph("1"), ph("2"), ph("3"), ph("4")];
  assert.deepStrictEqual(M.toResolve(q, 1), [1, 2, 3]); // the default window is WINDOW
  assert.deepStrictEqual(M.toResolve(q, -1), [0, 1]); // nothing current yet
  assert.deepStrictEqual(M.toResolve(q, 4, 0), [4]);
  assert.deepStrictEqual(M.toResolve([], 0), []);
  for (const block of M.REPLACE_BLOCKS) assert.equal(M.canReplace(t("dQw4w9WgXcQ"), block), true);
  for (const block of ["LOGIN_REQUIRED", "BOT_WALL", "NETWORK", null]) assert.equal(M.canReplace(t("dQw4w9WgXcQ"), block), false);
  assert.equal(M.canReplace(t("dQw4w9WgXcQ"), "GONE", { enabled: false }), false);
  assert.equal(M.canReplace(t("nd:abc"), "GONE"), false); // a library file has no other upload
  assert.equal(M.canReplace(t("mc:"), "GONE"), false);
  assert.equal(M.canReplace(t("dQw4w9WgXc"), "GONE"), false); // 10 chars: not a YouTube id
});

test("placeholder: junk durations are unknown, text is stringified, optional fields only when set", () => {
  assert.deepStrictEqual(M.placeholder({ title: 5, artist: null, duration: "abc" }, "pq:1"),
    { id: "pq:1", q: true, title: "5", artist: "null", channel: "null", duration: null });
  assert.equal(M.placeholder({ duration: -3 }, "pq:2").duration, null);
  assert.equal(M.placeholder({ duration: "200" }, "pq:3").duration, 200);
  assert.deepStrictEqual(M.placeholder(undefined, "pq:4"), { id: "pq:4", q: true, title: "", artist: "", channel: "", duration: null });
  assert.equal(M.isPlaceholder({ q: "true" }), false); // only the boolean marks one
  assert.equal(M.isPlaceholder(null), false);
});

test("swap: unknown id or a track without an id changes nothing; dropping keeps the current track sensible", () => {
  const q = [t("a"), ph("p"), t("b")];
  assert.deepStrictEqual(M.swap(q, 1, "pq:zz", t("x")), { queue: q, index: 1, at: -1, dropped: false });
  assert.deepStrictEqual(M.swap(q, 1, "pq:p", { title: "no id" }), { queue: q, index: 1, at: -1, dropped: false });
  assert.deepStrictEqual(M.swap(q, 1, "pq:p", null).at, -1);
  // the current placeholder resolves to a song already queued after it: dropped, that song becomes current
  let r = M.swap(q, 1, "pq:p", t("b"));
  assert.deepStrictEqual([r.queue.map((x) => x.id), r.index, r.at, r.dropped], [["a", "b"], 1, 1, true]);
  // a history placeholder dropped: the current index moves back with its track
  r = M.swap([ph("p"), t("a"), t("b")], 2, "pq:p", t("a"));
  assert.deepStrictEqual([r.queue.map((x) => x.id), r.index], [["a", "b"], 1]);
  // the last entry, current, dropped: the new last one is current
  r = M.swap([t("a"), ph("p")], 1, "pq:p", t("a"));
  assert.deepStrictEqual([r.queue.map((x) => x.id), r.index], [["a"], 0]);
});

test("segments: cleaning, boundaries, targets", () => {
  const raw = [{ start: 30, end: 40, category: "sponsor" }, { start: 5, end: 5 }, { start: -1, end: 3 }, { start: NaN, end: 9 },
    { start: 0, end: Infinity }, null, "x", { start: 2, end: 8 }];
  const s = snapshot(raw);
  const segs = M.cleanSegments(raw);
  assert.deepStrictEqual(segs, [{ start: 2, end: 8 }, { start: 30, end: 40 }]); // fresh objects, extra fields dropped
  s.same(assert);
  assert.deepStrictEqual(M.cleanSegments(null), []);
  assert.deepStrictEqual(M.cleanSegments({ start: 1, end: 2 }), []);
  const done = new Set();
  assert.equal(M.segmentAt(segs, done, 1.94), -1);
  assert.equal(M.segmentAt(segs, done, 1.95), 0); // SEG_EARLY: entered by the first timeupdate
  assert.equal(M.segmentAt(segs, done, 7.49), 0);
  assert.equal(M.segmentAt(segs, done, 7.5), -1); // SEG_TAIL: let the last half second play out
  assert.equal(M.segmentAt(null, done, 3), -1);
  assert.deepStrictEqual(M.entered(segs, 8), []); // the end itself is outside
  assert.deepStrictEqual(M.entered(segs, 2), [0]);
  assert.deepStrictEqual(M.entered(null, 2), []);
  assert.equal(M.segmentTarget({ start: 190, end: 200 }, 0), 200); // unknown length: the segment's end
  assert.equal(M.segmentTarget({ start: 190, end: 200 }, 195), 194.75); // kept inside the track
  assert.equal(M.segmentTarget({ start: 0, end: 0.2 }, 0.1), 0);
});

test("swap and toResolve never mutate the queue or the track", () => {
  const q = [t("a"), ph("p", { key: "k" }), { ...ph("r"), auto: true }, t("b")], track = t("x");
  const s = snapshot(q, track);
  M.toResolve(q, 0);
  M.swap(q, 0, "pq:p", track);
  M.swap(q, 0, "pq:r", track);
  M.swap(q, 0, "pq:p", t("b"));
  s.same(assert);
  assert.equal(M.swap(q, 0, "pq:r", track).queue[2].auto, true); // an autoplay entry stays one, as a new object
  assert.equal(track.auto, undefined);
});
