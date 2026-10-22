"use strict";
// library-core (L): the stream id of a kept track, the listen counting rule, played time between ticks, listen reports.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { load, snapshot } = require("./harness");

const L = load("library-core");

const yt = { id: "dQw4w9WgXcQ" };

test("streamId: the Navidrome copy only when the library is on, preferred, not failed and its id is sane", () => {
  assert.equal(L.streamId(yt, { ndId: "abc123", ndOn: true }), "nd:abc123");
  assert.equal(L.streamId({ id: "mc:123" }, { ndId: "q1", ndOn: true }), "nd:q1"); // a kept Monochrome track too
  assert.equal(L.streamId(yt, { ndId: "abc123", ndOn: false }), yt.id);
  assert.equal(L.streamId(yt, { ndId: "abc123", ndOn: true, prefer: false }), yt.id);
  assert.equal(L.streamId(yt, { ndId: "abc123", ndOn: true, failed: true }), yt.id); // fallback after the copy failed
  assert.equal(L.streamId(yt, { ndId: null, ndOn: true }), yt.id); // not scanned yet
  assert.equal(L.streamId(yt), yt.id); // no options at all
  assert.equal(L.streamId({ id: "nd:zz" }, { ndId: "abc", ndOn: true }), "nd:zz"); // already a library track
});

test("streamId: odd Navidrome ids and missing tracks", () => {
  for (const ndId of ["../x y", "", "a/b", "a?b", 123, "x".repeat(129)]) {
    assert.equal(L.streamId(yt, { ndId, ndOn: true }), yt.id, JSON.stringify(ndId));
  }
  assert.equal(L.streamId(yt, { ndId: "x".repeat(128), ndOn: true }), "nd:" + "x".repeat(128)); // the length limit itself
  assert.equal(L.streamId(yt, { ndId: "a.b-c_D9", ndOn: true }), "nd:a.b-c_D9");
  for (const t of [null, undefined, {}, { id: "" }, { id: 42 }]) assert.equal(L.streamId(t, { ndId: "a", ndOn: true }), null);
});

test("counts: 30 s, or half of a known length", () => {
  assert.equal(L.COUNT_S, 30);
  assert.equal(L.COUNT_SHARE, 0.5);
  assert.equal(L.counts(30, 600), true);
  assert.equal(L.counts(29.9, 600), false);
  assert.equal(L.counts(100, 200), true);
  assert.equal(L.counts(25, 50), true); // exactly half, under 30 s
  assert.equal(L.counts(24.9, 50), false);
  assert.equal(L.counts(20, 40), true);
  assert.equal(L.counts(20, 0), false); // unknown length: only the 30 s rule
  assert.equal(L.counts(20, null), false);
  assert.equal(L.counts(0, 0), false);
});

test("playedDelta: steady ticks count, seeks and going back count nothing", () => {
  assert.equal(L.playedDelta(null, null, 5, 1000), 0); // first tick
  assert.equal(L.playedDelta(10, null, 11, 1000), 0);
  assert.ok(Math.abs(L.playedDelta(10, 0, 10.25, 250) - 0.25) < 1e-9);
  assert.equal(L.playedDelta(10, 0, 40, 250), 0); // seek forward
  assert.equal(L.playedDelta(40, 0, 10, 250), 0); // seek back
  assert.equal(L.playedDelta(10, 0, 10, 250), 0); // paused
  assert.equal(L.playedDelta(10, 0, 25, 15000), 15); // a throttled background tick still counts
});

test("playedDelta: 1.5 s of slack over the wall clock, playback rate, a wall clock going back", () => {
  assert.equal(L.playedDelta(0, 0, 2.5, 1000), 2.5); // 1 s of wall clock + 1.5 s slack: allowed exactly
  assert.equal(L.playedDelta(0, 0, 2.51, 1000), 0);
  assert.ok(Math.abs(L.playedDelta(10, 0, 20, 5000, 2) - 10) < 1e-9); // 2x speed: track seconds
  assert.equal(L.playedDelta(0, 0, 2.6, 1000, 0.5), 0); // slower than 1x is still allowed 1x
  assert.equal(L.playedDelta(0, 0, 2.5, 1000, "fast"), 2.5); // a junk rate counts as 1x
  assert.equal(L.playedDelta(0, 5000, 1.5, 1000), 1.5); // wall clock went back: only the slack is allowed
  assert.equal(L.playedDelta(0, 5000, 1.6, 1000), 0);
});

test("playedDelta: a 35 s play with one seek in the middle accumulates the time actually heard", () => {
  let played = 0, t = 0, w = 0, lastT = null, lastW = null;
  for (let i = 0; i < 140; i++) {
    if (i === 70) { t += 60; lastT = null; } // the seeking handler resets the last position
    played += L.playedDelta(lastT, lastW, t, w);
    lastT = t; lastW = w; t += 0.25; w += 250;
  }
  assert.ok(Math.abs(played - 34.5) < 0.01, `accumulated ${played}`);
});

const track = { id: "dQw4w9WgXcQ", title: "T", channel: "C", duration: 200, source: "yt", artists: [{ name: "A", id: null }], album: "Al", album_id: "MPRE1" };

test("listenReport: the POST /api/listens body, rounded and floored", () => {
  const l = { track, startedAt: 1700000000.7, played: 35.04, via: "nd", sent: null };
  const s = snapshot(l);
  assert.deepStrictEqual(L.listenReport(l, { skipped: true, now: 1700000040999 }), {
    id: track.id, title: "T", channel: "C", duration: 200, artists: [{ name: "A", id: null }], album: "Al", album_id: "MPRE1",
    played_s: 35, started_at: 1700000000, ended_at: 1700000040, skipped: true, source: "yt", via: "nd",
  });
  s.same(assert);
});

test("listenReport: optional fields are left out, missing ones get defaults", () => {
  const r = L.listenReport({ track: { id: "x" }, startedAt: 10, played: 1 }, { now: 20000 });
  assert.deepStrictEqual(r, { id: "x", title: "", channel: "", duration: null, played_s: 1, started_at: 10, ended_at: 20,
    skipped: false, source: "" });
  assert.equal(L.listenReport({ track: { id: "x" }, startedAt: 10, played: 1 }, { skipped: "yes", now: 0 }).skipped, true);
});

test("listenReport: nothing under MIN_REPORT_S, nothing without a track", () => {
  assert.equal(L.MIN_REPORT_S, 1);
  assert.equal(L.listenReport({ track, startedAt: 1000, played: 0.99 }), null);
  assert.equal(L.listenReport({ track, startedAt: 1000, played: NaN }), null);
  assert.equal(L.listenReport({ track, startedAt: 1000 }), null);
  assert.equal(L.listenReport({ startedAt: 1000, played: 50 }), null);
  assert.equal(L.listenReport(null), null);
  assert.notEqual(L.listenReport({ track, startedAt: 1000, played: 1 }, { now: 0 }), null);
});

test("listenReport: the same play is reported again only with more time or another skip flag", () => {
  const l = { track, startedAt: 1700000000, played: 35.04, sent: { played: 35, skipped: true } };
  assert.equal(L.listenReport(l, { skipped: true, now: 0 }), null);
  l.played = 35.3; // rounds to 35.3: under 0.5 s more
  assert.equal(L.listenReport(l, { skipped: true, now: 0 }), null);
  l.played = 35.5; // exactly 0.5 s more
  assert.equal(L.listenReport(l, { skipped: true, now: 0 }), null);
  l.played = 35.6;
  assert.equal(L.listenReport(l, { skipped: true, now: 0 }).played_s, 35.6);
  l.played = 35.3;
  assert.equal(L.listenReport(l, { skipped: false, now: 0 }).skipped, false); // the skip flag changed
});

test("listenReport: `now` defaults to the clock (mocked)", (t) => {
  t.mock.timers.enable({ apis: ["Date"], now: 1700000123456 });
  assert.equal(L.listenReport({ track, startedAt: 1700000000, played: 40 }).ended_at, 1700000123);
});
