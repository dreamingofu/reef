"use strict";
// player-core (PC): gesture, geometry and timing maths of the mini player, Now Playing and Up next.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { load, snapshot } = require("./harness");

const PC = load("player-core");

// node check for the player-core section of static/app.js (sliced between its sentinels).

const near = (x, y, eps = 1e-6) => assert.ok(Math.abs(x - y) <= eps, `${x} != ${y}`);

test("axisOf: slop, axis, left-edge exclusion, disallowed axes", () => {
  assert.equal(PC.axisOf(3, 4), null); // 5 px < 10
  assert.equal(PC.axisOf(12, 3, { x0: 100 }), "x");
  assert.equal(PC.axisOf(-12, 3, { x0: 100 }), "x");
  assert.equal(PC.axisOf(3, 12, { x0: 100 }), "y");
  assert.equal(PC.axisOf(12, 3, { x0: 23 }), "none"); // inside the 24 px back-swipe zone
  assert.equal(PC.axisOf(12, 3, { x0: 24 }), "x");
  assert.equal(PC.axisOf(3, 12, { x0: 5 }), "y"); // vertical drags from the edge are fine
  assert.equal(PC.axisOf(12, 3, { x0: 100, allowX: false }), "none");
  assert.equal(PC.axisOf(3, 12, { allowY: false }), "none");
  assert.equal(PC.EDGE, 24);
});

test("rubber: first 100 px move at most 30, then 1:1, sign kept", () => {
  near(PC.rubber(0), 0);
  near(PC.rubber(50), 15);
  near(PC.rubber(100), 30);
  near(PC.rubber(160), 90);
  near(PC.rubber(-160), -90);
  near(PC.rubber(60, 60, 20), 20); // queue rows
  near(PC.rubber(100, 60, 20), 60);
  near(PC.resist(100), 30);
});

test("velocity: px/s over the last 100 ms, 0 when the finger stopped", () => {
  const s = [{ t: 0, x: 0, y: 0 }, { t: 50, x: 50, y: 0 }, { t: 100, x: 100, y: -20 }, { t: 150, x: 150, y: -40 }];
  const v = PC.velocity(s);
  near(v.vx, 1000); near(v.vy, -400);
  assert.deepEqual(PC.velocity([{ t: 0, x: 0, y: 0 }]), { vx: 0, vy: 0 });
  assert.deepEqual(PC.velocity([]), { vx: 0, vy: 0 });
  const held = [{ t: 0, x: 0, y: 0 }, { t: 50, x: 80, y: 0 }, { t: 400, x: 80, y: 0 }, { t: 450, x: 80, y: 0 }];
  near(PC.velocity(held).vx, 0);
  assert.deepEqual(PC.velocity([{ t: 5, x: 0, y: 0 }, { t: 5, x: 9, y: 0 }]), { vx: 0, vy: 0 });
});

test("swipeCommit: a third of the width or a 1000 px/s flick the same way", () => {
  assert.equal(PC.swipeCommit(-130, 0, 390), -1);
  assert.equal(PC.swipeCommit(130, 0, 390), 1);
  assert.equal(PC.swipeCommit(-120, -200, 390), 0);
  assert.equal(PC.swipeCommit(-40, -1200, 390), -1);
  assert.equal(PC.swipeCommit(-40, 1200, 390), 0); // flicked back: no
  assert.equal(PC.swipeCommit(-4, -3000, 390), 0); // a twitch
  assert.equal(PC.swipeCommit(-117, 0, 390, { frac: 0.3 }), -1); // the art: 30%
  assert.equal(PC.swipeCommit(150, 0, 390, { frac: 0.4 }), 0); // queue rows: 40%
  assert.equal(PC.swipeCommit(156, 0, 390, { frac: 0.4 }), 1);
});

test("miniVertical and npClose", () => {
  assert.equal(PC.miniVertical(-30, 0), "open");
  assert.equal(PC.miniVertical(-10, -600), "open");
  assert.equal(PC.miniVertical(33, 0), "dismiss");
  assert.equal(PC.miniVertical(12, 900), "dismiss");
  assert.equal(PC.miniVertical(20, 100), null);
  assert.equal(PC.miniVertical(-10, 100), null);
  assert.equal(PC.npClose(120, 0), true);
  assert.equal(PC.npClose(40, 900), true);
  assert.equal(PC.npClose(10, 900), false);
  assert.equal(PC.npClose(80, 100), false);
});

test("pull-up: threshold clamp(5% h, 14, 56), velocities, settle", () => {
  near(PC.pullThreshold(667), 33.35);
  near(PC.pullThreshold(200), 14);
  near(PC.pullThreshold(2000), 56);
  assert.equal(PC.pullSettle(false, -40, 0, 667), true);
  assert.equal(PC.pullSettle(false, -20, 0, 667), false);
  assert.equal(PC.pullSettle(false, -5, -600, 667), true);
  assert.equal(PC.pullSettle(false, -80, 500, 667), false); // dragged up, flicked back down
  assert.equal(PC.pullSettle(true, 100, 0, 667), true); // under a quarter: stays open
  assert.equal(PC.pullSettle(true, 200, 0, 667), false);
  assert.equal(PC.pullSettle(true, 20, 460, 667), false);
  assert.equal(PC.pullSettle(true, 250, -600, 667), true);
});

test("pullLook: fade 1-f/.4, art 1-.1f, radius 28->0, clamped", () => {
  assert.deepEqual(PC.pullLook(0), { fade: 1, scale: 1, radius: 28 });
  const h = PC.pullLook(0.2); near(h.fade, 0.5); near(h.scale, 0.98); near(h.radius, 22.4);
  assert.deepEqual(PC.pullLook(1), { fade: 0, scale: 0.9, radius: 0 });
  assert.deepEqual(PC.pullLook(2), PC.pullLook(1));
  assert.deepEqual(PC.pullLook(-1), PC.pullLook(0));
});

test("settle: 0 -> 1, monotonic, no overshoot", () => {
  assert.equal(PC.settle(0), 0);
  assert.equal(PC.settle(380), 1);
  assert.equal(PC.settle(1000), 1);
  let prev = 0;
  for (let t = 10; t <= 380; t += 10) { const x = PC.settle(t); assert.ok(x >= prev && x <= 1); prev = x; }
  assert.ok(PC.settle(190) > 0.8); // most of the way by half time: no slow start
});

test("seekFrac and ringOffset", () => {
  near(PC.seekFrac(28, 28, 300), 0);
  near(PC.seekFrac(178, 28, 300), 0.5);
  near(PC.seekFrac(400, 28, 300), 1);
  near(PC.seekFrac(0, 28, 300), 0);
  near(PC.seekFrac(10, 0, 0), 0);
  const c = 2 * Math.PI * 24.75;
  near(PC.ringOffset(0, c), c); near(PC.ringOffset(1, c), 0); near(PC.ringOffset(0.25, c), c * 0.75);
  near(PC.ringOffset(NaN, c), c); near(PC.ringOffset(3, c), 0);
});

test("tapSeek: double tap seeks 5 s, each tap within 1 s adds 5, the other side starts over", () => {
  let s = PC.tapSeek(null, 0, 1);
  assert.equal(s.seek, 0);
  s = PC.tapSeek(s, 200, 1); assert.equal(s.seek, 5); assert.equal(s.total, 5);
  s = PC.tapSeek(s, 900, 1); assert.equal(s.seek, 5); assert.equal(s.total, 10);
  s = PC.tapSeek(s, 1800, 1); assert.equal(s.total, 15);
  s = PC.tapSeek(s, 3000, 1); assert.equal(s.seek, 0); // > 1 s: a new first tap
  s = PC.tapSeek(s, 3500, 1); assert.equal(s.seek, 0); // > 350 ms after the first tap: not a double tap
  s = PC.tapSeek(s, 3700, -1); assert.equal(s.seek, 0); // other half
  s = PC.tapSeek(s, 3900, -1); assert.equal(s.seek, -5); assert.equal(s.total, 5);
});

test("neighbours: prev/next indexes, repeat-all wraps next only", () => {
  assert.deepEqual(PC.neighbours(5, 2), { prev: 1, next: 3 });
  assert.deepEqual(PC.neighbours(5, 0), { prev: -1, next: 1 });
  assert.deepEqual(PC.neighbours(5, 4), { prev: 3, next: -1 });
  assert.deepEqual(PC.neighbours(5, 4, "all"), { prev: 3, next: 0 });
  assert.deepEqual(PC.neighbours(1, 0, "all"), { prev: -1, next: -1 });
  assert.deepEqual(PC.neighbours(0, -1), { prev: -1, next: -1 });
  assert.deepEqual(PC.neighbours(3, 7), { prev: -1, next: -1 });
});

const T = (...ids) => ids.map((id) => ({ id }));
const ids = (q) => q.map((t) => t.id).join("");

test("moveTrack: reorders, the current track stays current, input untouched", () => {
  const q = T("a", "b", "c", "d", "e");
  let r = PC.moveTrack(q, 1, 4, 2); assert.equal(ids(r.queue), "abecd"); assert.equal(r.index, 1);
  r = PC.moveTrack(q, 1, 2, 4); assert.equal(ids(r.queue), "abdec"); assert.equal(r.index, 1);
  r = PC.moveTrack(q, 2, 0, 3); assert.equal(ids(r.queue), "bcdae"); assert.equal(r.queue[r.index].id, "c");
  r = PC.moveTrack(q, 2, 4, 0); assert.equal(r.queue[r.index].id, "c");
  r = PC.moveTrack(q, 2, 2, 4); assert.equal(r.queue[r.index].id, "c");
  r = PC.moveTrack(q, 1, 3, 3); assert.equal(ids(r.queue), "abcde");
  r = PC.moveTrack(q, 1, 9, 1); assert.equal(ids(r.queue), "abcde");
  assert.equal(ids(q), "abcde");
});

test("dropIndex: rounds rows, clamps to the block", () => {
  assert.equal(PC.dropIndex(3, 0, 60, 2, 8), 3);
  assert.equal(PC.dropIndex(3, 89, 60, 2, 8), 4);
  assert.equal(PC.dropIndex(3, 91, 60, 2, 8), 5);
  assert.equal(PC.dropIndex(3, -500, 60, 2, 8), 2);
  assert.equal(PC.dropIndex(3, 5000, 60, 2, 8), 8);
  assert.equal(PC.dropIndex(3, 100, 0, 2, 8), 3);
});

test("reinsert: undo a removal (upcoming and history), not twice", () => {
  // removed "d" (index 3) while "b" (1) was current
  let r = PC.reinsert(T("a", "b", "c", "e"), 1, { id: "d" }, 2);
  assert.equal(ids(r.queue), "abcde"); assert.equal(r.index, 1);
  // removed "a" (index 0) while "c" (2) was current: index became 1
  r = PC.reinsert(T("b", "c", "d"), 1, { id: "a" }, -2);
  assert.equal(ids(r.queue), "abcd"); assert.equal(r.queue[r.index].id, "c");
  // already back
  r = PC.reinsert(T("a", "b", "d"), 1, { id: "d" }, 2);
  assert.equal(ids(r.queue), "abd");
  // the queue moved on meanwhile: relative to the new current
  r = PC.reinsert(T("a", "b", "c"), 2, { id: "x" }, 1);
  assert.equal(ids(r.queue), "abcx"); assert.equal(r.index, 2);
  r = PC.reinsert(T("a"), 0, { id: "x" }, 0);
  assert.equal(ids(r.queue), "a");
});

test("queueStats, bgStyle, marquee", () => {
  assert.deepEqual(PC.queueStats([{ duration: 200 }, { duration: null }, { duration: 100.4 }, {}]), { count: 4, seconds: 300 });
  assert.deepEqual(PC.queueStats(null), { count: 0, seconds: 0 });
  assert.equal(PC.bgStyle("tint"), "tint");
  assert.equal(PC.bgStyle("nope"), "ambient");
  assert.equal(PC.bgStyle(undefined), "ambient");
  assert.deepEqual(PC.BGS, ["ambient", "immersive", "tint", "black"]);
  assert.equal(PC.marquee(200, 200), null);
  assert.equal(PC.marquee(200.5, 200), null);
  const m = PC.marquee(260, 200, { delay: 3000, speed: 30, gap: 40 });
  assert.equal(m.dist, 300); assert.equal(m.travel, 10000); assert.equal(m.total, 13000);
});

test("sameTrack: a delayed move runs only on the track it was asked for", () => {
  const then = { token: 41, id: "a" };
  assert.equal(PC.sameTrack(then, { token: 41, id: "a" }), true);
  assert.equal(PC.sameTrack(then, { token: 42, id: "a" }), false); // same song loaded again (a new play)
  assert.equal(PC.sameTrack(then, { token: 41, id: "b" }), false); // a placeholder swapped in place
  assert.equal(PC.sameTrack(then, { token: 42, id: "b" }), false); // skipped / ended / recovery moved on
  assert.equal(PC.sameTrack({ token: 1, id: null }, { token: 1, id: null }), false); // nothing was playing
  assert.equal(PC.sameTrack(null, { token: 1, id: "a" }), false);
  assert.equal(PC.sameTrack(then, null), false);
});

test("indexOfId: a swipe's target found again by id", () => {
  const q = T("a", "b", "c");
  assert.equal(PC.indexOfId(q, "b"), 1);
  // the queue changed between the swipe and its commit (a row removed above, a reorder)
  assert.equal(PC.indexOfId(T("b", "c"), "b"), 0);
  assert.equal(PC.indexOfId(T("c", "a", "b"), "b"), 2);
  assert.equal(PC.indexOfId(T("a", "c"), "b"), -1); // gone: the move is dropped
  assert.equal(PC.indexOfId(q, undefined), -1);
  assert.equal(PC.indexOfId(null, "a"), -1);
});

// ---- defaults, exhaustive queue edits, inputs never mutated ----
test("defaults: SLOP before an axis, the left-edge zone, clamp", () => {
  assert.equal(PC.SLOP, 10);
  assert.equal(PC.EDGE, 24);
  assert.equal(PC.axisOf(PC.SLOP - 0.1, 0), null);
  assert.equal(PC.axisOf(PC.SLOP, 0), "x");
  assert.equal(PC.axisOf(6, 8), "y");
  assert.equal(PC.axisOf(8, 8), "y"); // a tie goes vertical
  assert.equal(PC.axisOf(8, 6, { x0: PC.EDGE - 1 }), "none"); // Safari's back swipe
  assert.equal(PC.axisOf(8, 6, { x0: PC.EDGE }), "x");
  assert.equal(PC.clamp(5, 0, 3), 3);
  assert.equal(PC.clamp(-1, 0, 3), 0);
  assert.equal(PC.clamp(2, 0, 3), 2);
});

test("moveTrack: for every move in a 5-track queue the current track stays current and nothing is lost", () => {
  const q = T("a", "b", "c", "d", "e");
  for (let index = 0; index < 5; index++) for (let from = 0; from < 5; from++) for (let to = 0; to < 5; to++) {
    const r = PC.moveTrack(q, index, from, to), what = `index ${index} from ${from} to ${to}`;
    assert.equal(r.queue[r.index].id, q[index].id, what);
    assert.equal(r.queue[to].id, q[from].id, what);
    assert.equal(ids(r.queue.slice().sort((x, y) => (x.id < y.id ? -1 : 1))), "abcde", what);
  }
});

test("reinsert undoes every single removal exactly (queue and current index)", () => {
  const q = T("a", "b", "c", "d", "e");
  for (let index = 0; index < 5; index++) for (let i = 0; i < 5; i++) {
    if (i === index) continue; // the current track isn't removed by a row swipe
    const removed = q.filter((_, k) => k !== i);
    const r = PC.reinsert(removed, i < index ? index - 1 : index, q[i], i - index);
    assert.deepStrictEqual([ids(r.queue), r.index], ["abcde", index], `index ${index} removed ${i}`);
  }
});

test("queue edits and pointer samples are never mutated; invalid moves are no-ops", () => {
  const q = T("a", "b", "c", "d"), samples = [{ t: 0, x: 0, y: 0 }, { t: 50, x: 10, y: 5 }, { t: 100, x: 30, y: 5 }];
  const s = snapshot(q, samples);
  PC.moveTrack(q, 1, 0, 3); PC.reinsert(q, 1, { id: "z" }, 1); PC.velocity(samples); PC.queueStats(q);
  PC.neighbours(q.length, 1, "all"); PC.indexOfId(q, "c");
  s.same(assert);
  for (const [from, to] of [[-1, 2], [0, 4], [2, 2], [NaN, 1]]) assert.deepStrictEqual(PC.moveTrack(q, 1, from, to), { queue: q, index: 1 });
  assert.notEqual(PC.moveTrack(q, 1, 2, 2).queue, q);
  assert.deepStrictEqual(PC.reinsert(q, 1, { id: "z" }, 0), { queue: q, index: 1 }); // rel 0: nowhere to put it back
  assert.deepStrictEqual(PC.reinsert(q, 1, null, 1), { queue: q, index: 1 });
  assert.deepStrictEqual(PC.velocity([{ t: 5, x: 0, y: 0 }, { t: 5, x: 9, y: 9 }]), { vx: 0, vy: 0 }); // same timestamp
  assert.deepStrictEqual(PC.velocity(null), { vx: 0, vy: 0 });
});
