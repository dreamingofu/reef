"use strict";
// queue-core (Q): shuffle / unshuffle, play next, add to queue, autoplay block, removal, next / previous.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { load, snapshot } = require("./harness");

const Q = load("queue-core");

// Extracts the real queue core from static/app.js (between sentinel comments) and checks it.

const T = (n) => Array.from({ length: n }, (_, i) => ({ id: "t" + i, title: "T" + i }));
const ids = (q) => q.map((t) => t.id);
const sorted = (q) => ids(q).slice().sort();

// seeded rand so failures reproduce
let seed = 42;
const rand = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648);

test("shuffleUpcoming keeps history+current in place, same multiset, snapshot", () => {
  const q = T(10), before = ids(q);
  for (let k = 0; k < 50; k++) {
    const r = Q.shuffleUpcoming(q, 3, rand);
    assert.deepEqual(ids(r.queue.slice(0, 4)), before.slice(0, 4));
    assert.deepEqual(sorted(r.queue), sorted(q));
    assert.deepEqual(ids(r.original), before);
  }
  assert.deepEqual(ids(q), before, "input not mutated");
  let moved = false;
  for (let k = 0; k < 20 && !moved; k++) moved = ids(Q.shuffleUpcoming(q, 3, rand).queue).join() !== before.join();
  assert.ok(moved, "actually shuffles");
});

test("shuffleFrom: chosen track first at index 0, same multiset", () => {
  const q = T(8);
  const r = Q.shuffleFrom(q, 5, rand);
  assert.equal(r.index, 0);
  assert.equal(r.queue[0].id, "t5");
  assert.deepEqual(sorted(r.queue), sorted(q));
  assert.deepEqual(ids(r.original), ids(q));
});

test("unshuffle restores original order and keeps current", () => {
  const q = T(10);
  const r = Q.shuffleUpcoming(q, 2, rand);
  const at = 6, current = r.queue[at].id; // played forward a few tracks while shuffled
  const u = Q.unshuffle(r.queue, at, r.original);
  assert.deepEqual(ids(u.queue), ids(q));
  assert.equal(u.queue[u.index].id, current);
});

test("unshuffle after shuffleFrom restores list order, current found by id", () => {
  const q = T(6);
  const r = Q.shuffleFrom(q, 4, rand);
  const u = Q.unshuffle(r.queue, 0, r.original);
  assert.deepEqual(ids(u.queue), ids(q));
  assert.equal(u.index, 4);
});

test("added-while-shuffled survives unshuffle (appended in queue order); removed stays removed", () => {
  const q = T(6);
  let r = Q.shuffleUpcoming(q, 1, rand);
  let queue = Q.insertNext(r.queue, 1, { id: "x1" });
  queue = Q.append(queue, 1, { id: "x2" });
  const gone = queue[4].id;
  queue = Q.removeAt(queue, 1, 4).queue;
  const u = Q.unshuffle(queue, 1, r.original);
  assert.deepEqual(ids(u.queue), [...ids(q).filter((id) => id !== gone), "x1", "x2"]);
  assert.equal(u.queue[u.index].id, "t1");
});

test("unshuffle with no snapshot keeps queue order", () => {
  const q = T(4);
  const u = Q.unshuffle(q, 2, null);
  assert.deepEqual(ids(u.queue), ids(q));
  assert.equal(u.index, 2);
  const e = Q.unshuffle([], -1, []);
  assert.deepEqual(e, { queue: [], index: -1 });
});

test("insertNext puts the track right after current; dedupe moves an upcoming copy", () => {
  const q = T(5);
  assert.deepEqual(ids(Q.insertNext(q, 1, { id: "x" })), ["t0", "t1", "x", "t2", "t3", "t4"]);
  assert.deepEqual(ids(Q.insertNext(q, 1, q[4])), ["t0", "t1", "t4", "t2", "t3"]);
  // a copy in history (before current) is not upcoming: insert, don't move
  assert.deepEqual(ids(Q.insertNext(q, 2, q[0])), ["t0", "t1", "t2", "t0", "t3", "t4"]);
  // successive play-next: the newest goes first (right after current)
  assert.deepEqual(ids(Q.insertNext(Q.insertNext(q, 0, { id: "a" }), 0, { id: "b" })).slice(0, 3), ["t0", "b", "a"]);
  assert.deepEqual(ids(q), ["t0", "t1", "t2", "t3", "t4"], "input not mutated");
});

test("append adds at the end; dedupe moves an upcoming copy to the end", () => {
  const q = T(5);
  assert.deepEqual(ids(Q.append(q, 1, { id: "x" })), ["t0", "t1", "t2", "t3", "t4", "x"]);
  assert.deepEqual(ids(Q.append(q, 1, q[2])), ["t0", "t1", "t3", "t4", "t2"]);
  assert.deepEqual(ids(Q.append(q, 3, q[1])), ["t0", "t1", "t2", "t3", "t4", "t1"]);
  assert.deepEqual(ids(Q.append([], -1, { id: "x" })), ["x"]);
});

test("removeAt before / at / after current, and out of range", () => {
  const q = T(5);
  let r = Q.removeAt(q, 2, 0);
  assert.deepEqual([ids(r.queue), r.index], [["t1", "t2", "t3", "t4"], 1]);
  assert.equal(r.queue[r.index].id, "t2");
  r = Q.removeAt(q, 2, 2);
  assert.deepEqual([ids(r.queue), r.index], [["t0", "t1", "t3", "t4"], 2]); // next one becomes current
  r = Q.removeAt(q, 4, 4);
  assert.deepEqual([ids(r.queue), r.index], [["t0", "t1", "t2", "t3"], 3]); // removing the last current
  r = Q.removeAt(q, 2, 3);
  assert.deepEqual([ids(r.queue), r.index], [["t0", "t1", "t2", "t4"], 2]);
  r = Q.removeAt(q, 2, 9);
  assert.deepEqual([ids(r.queue), r.index], [ids(q), 2]);
  r = Q.removeAt([{ id: "a" }], 0, 0);
  assert.deepEqual([r.queue, r.index], [[], -1]);
});

test("clearUpcoming keeps history + current", () => {
  assert.deepEqual(ids(Q.clearUpcoming(T(5), 2)), ["t0", "t1", "t2"]);
});

test("nextIndex: mid-queue, end with repeat off/all/one, empty", () => {
  assert.equal(Q.nextIndex(5, 2, "off"), 3);
  assert.equal(Q.nextIndex(5, 2, "one"), 3);
  assert.equal(Q.nextIndex(5, 4, "off"), -1);
  assert.equal(Q.nextIndex(5, 4, "all"), 0);
  assert.equal(Q.nextIndex(5, 4, "one"), -1);
  assert.equal(Q.nextIndex(1, 0, "all"), 0);
  assert.equal(Q.nextIndex(0, -1, "all"), -1);
});

test("prevIndex: back one, restart at the start", () => {
  assert.equal(Q.prevIndex(5, 3), 2);
  assert.equal(Q.prevIndex(5, 0), -1);
  assert.equal(Q.prevIndex(0, -1), -1);
});

// --- Play next survives unshuffle (coordinator follow-up) ---
// Simulates one user flow with the app's own calls; `roundtrip` mimics the session save/restore (JSON).
const roundtrip = (st) => JSON.parse(JSON.stringify(st));
function flow({ persistBetween = false } = {}) {
  const q = T(8); // t0 t1 = history, t2 = current, t3..t7 = original upcoming
  let st = { queue: q, index: 2, original: null };
  const on = Q.shuffleUpcoming(st.queue, st.index, rand);
  st = { ...st, queue: on.queue, original: on.original };
  if (persistBetween) st = roundtrip(st);
  const pn = Q.playNext(st.queue, st.index, { id: "A" }, st.original);
  st = { ...st, queue: pn.queue, original: pn.original };
  if (persistBetween) st = roundtrip(st);
  st = { ...st, queue: Q.append(st.queue, st.index, { id: "B" }) };
  if (persistBetween) st = roundtrip(st);
  const off = Q.unshuffle(st.queue, st.index, st.original);
  return { q, shuffledQueue: st.queue, off };
}

test("shuffle on -> Play next A -> Add to queue B -> shuffle off = [history, current, A, original upcoming, B]", () => {
  const { shuffledQueue, off } = flow();
  assert.deepEqual(ids(shuffledQueue.slice(0, 4)), ["t0", "t1", "t2", "A"], "A is next while shuffled too");
  assert.equal(shuffledQueue.at(-1).id, "B");
  assert.deepEqual(ids(off.queue), ["t0", "t1", "t2", "A", "t3", "t4", "t5", "t6", "t7", "B"]);
  assert.equal(off.queue[off.index].id, "t2");
});

test("same flow survives a session save/restore (JSON round-trip) between every step", () => {
  const { off } = flow({ persistBetween: true });
  assert.deepEqual(ids(off.queue), ["t0", "t1", "t2", "A", "t3", "t4", "t5", "t6", "t7", "B"]);
  assert.equal(off.index, 2);
});

test("several Play nexts while shuffled keep their order (newest first) after unshuffle", () => {
  const q = T(5);
  const on = Q.shuffleUpcoming(q, 1, rand);
  let r = Q.playNext(on.queue, 1, { id: "A" }, on.original);
  r = Q.playNext(r.queue, 1, { id: "C" }, r.original);
  assert.deepEqual(ids(r.queue.slice(0, 4)), ["t0", "t1", "C", "A"]);
  const off = Q.unshuffle(r.queue, 1, r.original);
  assert.deepEqual(ids(off.queue), ["t0", "t1", "C", "A", "t2", "t3", "t4"]);
});

test("Play next of an original upcoming track while shuffled: moved, not duplicated, stays next after unshuffle", () => {
  const q = T(6);
  const on = Q.shuffleUpcoming(q, 1, rand);
  const r = Q.playNext(on.queue, 1, q[4], on.original);
  assert.deepEqual(sorted(r.queue), sorted(q));
  assert.equal(r.queue[2].id, "t4");
  const off = Q.unshuffle(r.queue, 1, r.original);
  assert.deepEqual(ids(off.queue), ["t0", "t1", "t4", "t2", "t3", "t5"]);
});

test("Play next when the current track was itself added while shuffled still lands right after it", () => {
  const q = T(4);
  const on = Q.shuffleUpcoming(q, 0, rand);
  let queue = Q.append(on.queue, 0, { id: "X" });           // added while shuffled
  const xi = queue.findIndex((t) => t.id === "X");           // ...and now playing
  const r = Q.playNext(queue, xi, { id: "A" }, on.original);
  assert.deepEqual(ids(r.original), ids(q), "snapshot untouched");
  const off = Q.unshuffle(r.queue, xi, r.original);
  assert.deepEqual(ids(off.queue), ["t0", "t1", "t2", "t3", "X", "A"]);
  assert.equal(off.queue[off.index].id, "X");
});

test("playNext without a snapshot (not shuffled) is plain insertNext", () => {
  const q = T(4);
  const r = Q.playNext(q, 1, { id: "A" }, null);
  assert.deepEqual(ids(r.queue), ["t0", "t1", "A", "t2", "t3"]);
  assert.equal(r.original, null);
});


// --- Phase 4: autoplay (auto items), radio fill ---
const A = (...names) => names.map((id) => ({ id, auto: true }));
const autos = (q) => q.filter((t) => t.auto).map((t) => t.id);

test("appendNew: skips ids already anywhere in the queue and dupes in the list; respects limit; marks auto", () => {
  const q = T(4); // t0..t3, current somewhere
  const radio = [{ id: "t1" }, { id: "r1" }, { id: "r1" }, { id: "t3" }, { id: "r2" }, { id: "r3" }, null, { id: "" }];
  const out = Q.appendNew(q, radio, { limit: 2, auto: true });
  assert.deepEqual(ids(out), ["t0", "t1", "t2", "t3", "r1", "r2"]);
  assert.deepEqual(autos(out), ["r1", "r2"]);
  assert.ok(!("auto" in radio[1]), "input tracks not mutated");
  assert.deepEqual(ids(q), ["t0", "t1", "t2", "t3"], "queue not mutated");
  const all = Q.appendNew(q, radio);
  assert.deepEqual(ids(all), ["t0", "t1", "t2", "t3", "r1", "r2", "r3"]);
  assert.deepEqual(autos(all), [], "not auto unless asked");
  assert.deepEqual(ids(Q.appendNew([], [{ id: "a" }, { id: "a" }])), ["a"]);
});

test("append (Add to queue) goes before the first upcoming autoplay item", () => {
  const q = [...T(3), ...A("a1", "a2")];
  assert.deepEqual(ids(Q.append(q, 1, { id: "x" })), ["t0", "t1", "t2", "x", "a1", "a2"]);
  // twice: in order, both before the block
  assert.deepEqual(ids(Q.append(Q.append(q, 1, { id: "x" }), 1, { id: "y" })), ["t0", "t1", "t2", "x", "y", "a1", "a2"]);
  // an upcoming autoplay copy is moved out of the block and becomes a manual item
  const m = Q.append(q, 1, { id: "a2" });
  assert.deepEqual(ids(m), ["t0", "t1", "t2", "a2", "a1"]);
  assert.deepEqual(autos(m), ["a1"]);
  // the current track is itself an autoplay item: the rest of the block still comes after
  const c = [...T(2), ...A("a1", "a2", "a3")];
  assert.deepEqual(ids(Q.append(c, 2, { id: "x" })), ["t0", "t1", "a1", "x", "a2", "a3"]);
  // an auto item in history (before current) is not "upcoming": no effect on placement
  const h = [...A("h1"), ...T(2)];
  assert.deepEqual(ids(Q.append(h, 1, { id: "x" })), ["h1", "t0", "t1", "x"]);
});

test("insertNext (Play next) lands before the autoplay block", () => {
  const q = [...T(2), ...A("a1", "a2")];
  assert.deepEqual(ids(Q.insertNext(q, 1, { id: "x" })), ["t0", "t1", "x", "a1", "a2"]);
  const m = Q.insertNext(q, 1, { id: "a2" });
  assert.deepEqual(ids(m), ["t0", "t1", "a2", "a1"]);
  assert.deepEqual(autos(m), ["a1"], "moved copy is manual now");
});

test("stripAuto removes only upcoming autoplay items (history + current stay)", () => {
  const q = [...A("h1"), { id: "t0" }, ...A("c"), { id: "m1" }, ...A("a1", "a2")];
  // current = "c" (index 2)
  const out = Q.stripAuto(q, 2);
  assert.deepEqual(ids(out), ["h1", "t0", "c", "m1"]);
  assert.deepEqual(ids(Q.stripAuto(T(3), 1)), ["t0", "t1", "t2"]);
  assert.equal(q.length, 6, "input not mutated");
});

test("upcomingParts splits manual and autoplay block with the block's queue index", () => {
  const q = [...T(3), ...A("a1", "a2")];
  let p = Q.upcomingParts(q, 0);
  assert.deepEqual([ids(p.manual), ids(p.auto), p.at], [["t1", "t2"], ["a1", "a2"], 3]);
  p = Q.upcomingParts(q, 3); // playing a1
  assert.deepEqual([ids(p.manual), ids(p.auto), p.at], [[], ["a2"], 4]);
  p = Q.upcomingParts(T(3), 1);
  assert.deepEqual([ids(p.manual), ids(p.auto), p.at], [["t2"], [], -1]);
  p = Q.upcomingParts(T(2), 1);
  assert.deepEqual([p.manual, p.auto, p.at], [[], [], -1]);
});

test("shuffleUpcoming leaves the autoplay block at the end, in order; unshuffle restores all", () => {
  const q = [...T(6), ...A("a1", "a2", "a3")];
  for (let k = 0; k < 30; k++) {
    const r = Q.shuffleUpcoming(q, 1, rand);
    assert.deepEqual(ids(r.queue.slice(0, 2)), ["t0", "t1"]);
    assert.deepEqual(ids(r.queue.slice(-3)), ["a1", "a2", "a3"]);
    assert.deepEqual(sorted(r.queue), sorted(q));
    const u = Q.unshuffle(r.queue, 1, r.original);
    assert.deepEqual(ids(u.queue), ids(q));
  }
});

test("autoplay flow: fill -> Add to queue -> Play next -> session round-trip -> autoplay off", () => {
  let queue = T(2), index = 0; // t0 playing, t1 left: <= 2 upcoming triggers a fill
  queue = Q.appendNew(queue, [{ id: "t0" }, { id: "r1" }, { id: "r2" }, { id: "r3" }], { limit: 20, auto: true });
  assert.deepEqual(ids(queue), ["t0", "t1", "r1", "r2", "r3"]);
  queue = Q.append(queue, index, { id: "B" });
  queue = Q.insertNext(queue, index, { id: "A" });
  assert.deepEqual(ids(queue), ["t0", "A", "t1", "B", "r1", "r2", "r3"]);
  queue = roundtrip(queue); // saveSession keeps `auto`
  assert.deepEqual(autos(queue), ["r1", "r2", "r3"]);
  index = 4; // now playing r1 (an autoplay item)
  queue = Q.stripAuto(queue, index);
  assert.deepEqual(ids(queue), ["t0", "A", "t1", "B", "r1"]);
  assert.equal(Q.nextIndex(queue.length, index, "off"), -1, "ends after the current autoplay track");
});

test("radio start: seed + radio replaces the upcoming part, deduped against what stays", () => {
  const q = T(5); // playing t2 (seed); t3, t4 replaced
  const radio = [{ id: "t2" }, { id: "x1" }, { id: "t0" }, { id: "x2" }, { id: "x1" }];
  const out = Q.appendNew(Q.clearUpcoming(q, 2), radio);
  assert.deepEqual(ids(out), ["t0", "t1", "t2", "x1", "x2"]);
  assert.deepEqual(autos(out), []);
});

// ---- determinism, boundaries, inputs never mutated ----
const seeded = (s) => () => ((s = (s * 1103515245 + 12345) % 2147483648) / 2147483648);

test("shuffled: a permutation, reproducible from a seeded rand, input untouched", () => {
  const q = T(12), s = snapshot(q);
  const a = Q.shuffled(q, seeded(1)), b = Q.shuffled(q, seeded(1)), c = Q.shuffled(q, seeded(2));
  assert.deepStrictEqual(ids(a), ids(b));
  assert.notDeepStrictEqual(ids(a), ids(c));
  assert.deepStrictEqual(sorted(a), sorted(q));
  assert.notEqual(a, q);
  s.same(assert);
  assert.deepStrictEqual(Q.shuffled([], seeded(1)), []);
  assert.deepStrictEqual(ids(Q.shuffled(T(1), () => 0.99)), ["t0"]);
});

test("shuffled: rand at its bounds (0 and just under 1) gives the Fisher-Yates extremes", () => {
  assert.deepStrictEqual(ids(Q.shuffled(T(4), () => 0)), ["t1", "t2", "t3", "t0"]);
  assert.deepStrictEqual(ids(Q.shuffled(T(4), () => 0.999999)), ["t0", "t1", "t2", "t3"]);
});

test("shuffled / shuffleFrom / shuffleUpcoming default to Math.random (mocked)", (t) => {
  t.mock.method(Math, "random", () => 0);
  assert.deepStrictEqual(ids(Q.shuffled(T(4))), ["t1", "t2", "t3", "t0"]);
  assert.deepStrictEqual(ids(Q.shuffleFrom(T(4), 2).queue), ["t2", "t1", "t3", "t0"]);
  assert.deepStrictEqual(ids(Q.shuffleUpcoming(T(5), 1).queue), ["t0", "t1", "t3", "t4", "t2"]);
  assert.ok(Math.random.mock.callCount() > 0);
});

test("no queue function mutates the queue, the snapshot or the tracks it's given; results are new arrays", () => {
  const queue = [...T(5), { id: "a1", auto: true }, { id: "a2", auto: true }], original = T(6).reverse();
  const track = { id: "t4" }, extra = [{ id: "n1" }, { id: "t0" }];
  const s = snapshot(queue, original, track, extra);
  const r = () => 0.5;
  const outs = [Q.shuffled(queue, r), Q.shuffleUpcoming(queue, 1, r).queue, Q.shuffleFrom(queue, 2, r).queue,
    Q.unshuffle(queue, 2, original).queue, Q.unshuffle(queue, 1, null).queue, Q.insertNext(queue, 1, track),
    Q.playNext(queue, 1, track, original).queue, Q.append(queue, 1, track), Q.appendNew(queue, extra, { auto: true }),
    Q.appendNew(queue, []), Q.stripAuto(queue, 1), Q.stripAuto(queue, 99), Q.removeAt(queue, 1, 3).queue,
    Q.removeAt(queue, 1, 99).queue, Q.clearUpcoming(queue, 1), Q.clearUpcoming(queue, 99), Q.upcomingParts(queue, 1).manual];
  Q.nextIndex(queue.length, 1, "all"); Q.prevIndex(queue.length, 1);
  s.same(assert);
  for (const out of outs) assert.notEqual(out, queue);
  assert.notEqual(Q.playNext(queue, 1, track, original).original, original);
  assert.notEqual(Q.playNext(queue, 1, { id: "zz" }, [{ id: "other" }]).original, original);
  assert.notEqual(Q.shuffleUpcoming(queue, 1, r).original, queue);
});

test("boundaries: empty queues, out-of-range indexes, a lost current track", () => {
  assert.deepStrictEqual(Q.shuffleUpcoming([], -1, () => 0), { queue: [], original: [] });
  assert.deepStrictEqual(ids(Q.shuffleFrom(T(1), 0, () => 0).queue), ["t0"]);
  for (const i of [-1, 5, NaN, undefined]) assert.deepStrictEqual(Q.removeAt(T(5), 2, i), { queue: T(5), index: 2 });
  assert.deepStrictEqual(Q.unshuffle(T(3), 5, T(3)), { queue: T(3), index: 2 }); // index past the end: the last one
  assert.equal(Q.prevIndex(3, 7), 2);
  assert.equal(Q.nextIndex(3, -1, "off"), 0);
  assert.deepStrictEqual(Q.upcomingParts([], -1), { manual: [], auto: [], at: -1 });
  assert.deepStrictEqual(ids(Q.appendNew(T(2), [{ id: "x" }, { id: "y" }], { limit: 0 })), ["t0", "t1"]);
  assert.deepStrictEqual(ids(Q.appendNew(T(2), null)), ["t0", "t1"]);
});
