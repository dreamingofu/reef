"use strict";
// sheet-core (SH): track menu rules, row swipes, sheet dismiss, sleep timer, Song info formatting.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { load, snapshot } = require("./harness");

const SH = load("sheet-core");

// node check for the sheet-core section of static/app.js (sliced between its sentinels).


const base = { src: "yt", placeholder: false, playing: false, context: "list", keep: "none", canKeep: true, artists: 1, album: true, hide: true, matched: false };

test("menuModel: a YouTube song in a list gets everything", () => {
  const m = SH.menuModel(base);
  assert.deepEqual(m.quick, ["next", "queue", "radio", "keep", "playlist", "like"]);
  assert.deepEqual(m.goto, ["artist", "album"]);
  assert.deepEqual(m.recs, ["dislike", "hide"]);
  assert.deepEqual(m.more, ["info", "copy", "youtube"]); // no fix: the user picked it directly
  assert.deepEqual(m.head, ["share", "info"]);
});

test("menuModel: the playing song has no Play next / Add to queue; the player adds fix + background", () => {
  const m = SH.menuModel({ ...base, playing: true, context: "player" });
  assert.deepEqual(m.quick, ["radio", "keep", "playlist", "like"]);
  assert.deepEqual(m.more, ["info", "fix", "copy", "youtube", "npbg"]);
});

test("menuModel: from the queue, Remove replaces Add to queue, fix is offered", () => {
  const m = SH.menuModel({ ...base, context: "queue" });
  assert.deepEqual(m.quick.slice(0, 2), ["next", "remove"]);
  assert.ok(m.more.includes("fix"));
});

test("menuModel: several artists -> a picker; none -> no Go to artist; no album page -> none", () => {
  assert.deepEqual(SH.menuModel({ ...base, artists: 2 }).goto, ["artists", "album"]);
  assert.deepEqual(SH.menuModel({ ...base, artists: 0, album: false }).goto, []);
});

test("menuModel: a placeholder (import row) can be queued, inspected and fixed, nothing else", () => {
  const m = SH.menuModel({ ...base, src: "pq", placeholder: true, keep: "none", canKeep: false, hide: false, artists: 0, album: false });
  assert.deepEqual(m.quick, ["next", "queue"]);
  assert.deepEqual(m.recs, []);
  assert.deepEqual(m.more, ["info", "fix"]);
  assert.deepEqual(m.head, ["info"]);
});

test("menuModel: library (nd:) tracks: no radio, dislike, copy, fix; keep shows as kept", () => {
  const m = SH.menuModel({ ...base, src: "nd", keep: "kept", canKeep: false, hide: false, context: "player" });
  assert.deepEqual(m.quick, ["next", "queue", "keep", "playlist", "like"]);
  assert.deepEqual(m.recs, []);
  assert.deepEqual(m.more, ["info", "npbg"]);
});

test("menuModel: Monochrome: no radio / copy / YouTube; keep hidden when it can't keep and isn't kept", () => {
  const m = SH.menuModel({ ...base, src: "mc", canKeep: false, hide: false });
  assert.deepEqual(m.quick, ["next", "queue", "playlist", "like"]);
  assert.deepEqual(m.more, ["info"]);
  assert.ok(SH.menuModel({ ...base, src: "mc", keep: "pending", canKeep: false }).quick.includes("keep"));
});

test("canFix / fixKey", () => {
  assert.equal(SH.canFix({ src: "yt", context: "list" }), false);
  assert.equal(SH.canFix({ src: "yt", context: "list", matched: true }), true);
  assert.equal(SH.canFix({ src: "mc", context: "queue" }), true);
  assert.equal(SH.canFix({ src: "nd", context: "player", matched: true }), false);
  assert.equal(SH.canFix({ src: "pq", placeholder: true }), true);
  assert.equal(SH.fixKey({ key: "k1", mkey: "k2" }), "k1");
  assert.equal(SH.fixKey({ mkey: "k2" }), "k2");
  assert.equal(SH.fixKey({ id: "x" }), null);
  assert.equal(SH.fixKey(null), null);
});

test("gridCols", () => {
  assert.deepEqual([1, 2, 3, 4, 5, 6].map(SH.gridCols), [3, 2, 3, 2, 3, 3]);
});

test("rowOffset / commit: 1:1 to 115 px (45% of narrow rows), then a third; flicks need the band", () => {
  assert.equal(SH.ROW_COMMIT, 115);
  assert.equal(SH.rowOffset(50, 335), 50);
  assert.equal(SH.rowOffset(-115, 335), -115);
  assert.equal(SH.rowOffset(145, 335), 125);
  assert.equal(SH.commitAt(200), 90);
  assert.equal(SH.commitAt(0), 115);
  assert.equal(SH.rowArmed(114, 335), false);
  assert.equal(SH.rowArmed(-115, 335), true);
  assert.equal(SH.rowCommit(120, 0, 335), 1);
  assert.equal(SH.rowCommit(-120, 0, 335), -1);
  assert.equal(SH.rowCommit(80, 0, 335), 0);
  assert.equal(SH.rowCommit(70, 1000, 335), 1); // a flick past the band
  assert.equal(SH.rowCommit(-70, -1000, 335), -1);
  assert.equal(SH.rowCommit(40, 1500, 335), 0); // the band not even uncovered
  assert.equal(SH.rowCommit(70, -1000, 335), 0); // flicked back
});

test("swipe prefs, actions, usability", () => {
  assert.deepEqual(SH.swipePrefs(null), { right: "next", left: "queue" });
  assert.deepEqual(SH.swipePrefs({ right: "like", left: "bogus" }), { right: "like", left: "queue" });
  const p = SH.swipePrefs({ right: "keep", left: "off" });
  assert.equal(SH.swipeAction(p, 1), "keep");
  assert.equal(SH.swipeAction(p, -1), "off");
  assert.equal(SH.swipeAction(p, 0), "off");
  assert.equal(SH.swipeUsable("next", { playing: true }), false);
  assert.equal(SH.swipeUsable("queue", {}), true);
  assert.equal(SH.swipeUsable("like", { placeholder: true }), false);
  assert.equal(SH.swipeUsable("keep", { keep: "none", canKeep: true }), true);
  assert.equal(SH.swipeUsable("keep", { keep: "kept", canKeep: true }), false);
  assert.equal(SH.swipeUsable("keep", { keep: "none", canKeep: true, placeholder: true }), false);
  assert.equal(SH.swipeUsable("off", {}), false);
});

test("sheetDismiss / sheetPull: distance, flick, flick back", () => {
  assert.equal(SH.sheetDismiss(150, 0, 600), true); // min(140, 210)
  assert.equal(SH.sheetDismiss(130, 0, 600), false);
  assert.equal(SH.sheetDismiss(110, 0, 300), true); // 35% of 300 = 105
  assert.equal(SH.sheetDismiss(20, 800, 600), true);
  assert.equal(SH.sheetDismiss(10, 800, 600), false);
  assert.equal(SH.sheetDismiss(200, -400, 600), false); // flicked back up
  assert.equal(SH.sheetPull(50), 50);
  assert.ok(SH.sheetPull(-400) === -24 && SH.sheetPull(-4) === -4);
});

test("sleep: clock parts, fraction, steppers, labels, +5", () => {
  assert.deepEqual(SH.clockParts(3723400), { h: "01", m: "02", s: "04" }); // rounds up
  assert.deepEqual(SH.clockParts(-5), { h: "00", m: "00", s: "00" });
  assert.equal(SH.sleepFrac(15 * 60000, 30), 0.5);
  assert.equal(SH.sleepFrac(99 * 60000, 30), 1);
  assert.equal(SH.sleepFrac(1000, 0), 0);
  assert.deepEqual(SH.stepCustom({ h: 0, m: 55 }, "m", 1), { h: 0, m: 0 });
  assert.deepEqual(SH.stepCustom({ h: 0, m: 0 }, "m", -1), { h: 0, m: 55 });
  assert.deepEqual(SH.stepCustom({ h: 11, m: 30 }, "h", 1), { h: 12, m: 0 });
  assert.deepEqual(SH.stepCustom({ h: 12, m: 0 }, "m", 1), { h: 12, m: 0 });
  assert.deepEqual(SH.stepCustom({ h: 0, m: 10 }, "h", -1), { h: 0, m: 10 });
  assert.equal(SH.customMinutes({ h: 1, m: 20 }), 80);
  assert.equal(SH.customMinutes({ h: 99, m: 0 }), SH.SLEEP_MAX);
  assert.equal(SH.durLabel(80), "1 h 20 min");
  assert.equal(SH.durLabel(120), "2 h");
  assert.equal(SH.durLabel(45), "45 min");
  const now = 1e6;
  assert.deepEqual(SH.extend(now + 60000, 30, now), { deadline: now + 360000, total: 30 });
  assert.deepEqual(SH.extend(now + 28 * 60000, 30, now), { deadline: now + 33 * 60000, total: 33 });
  assert.equal(SH.extend(now + SH.SLEEP_MAX * 60000, 720, now).deadline, now + SH.SLEEP_MAX * 60000);
});

test("song info formatting", () => {
  assert.equal(SH.fmtRate(44100), "44.1 kHz");
  assert.equal(SH.fmtRate(48000), "48 kHz");
  assert.equal(SH.fmtRate(null), "");
  assert.equal(SH.fmtKbps(127.6), "128 kbps");
  assert.equal(SH.fmtSize(3456789), "3.3 MB");
  assert.equal(SH.fmtSize(34567890), "33 MB");
  assert.equal(SH.fmtSize(2000), "2 KB");
  assert.equal(SH.fmtSize(0), "");
  assert.equal(SH.codecName("mp4a"), "AAC");
  assert.equal(SH.codecName("opus"), "Opus");
  assert.equal(SH.codecName("xyz"), "XYZ");
  assert.equal(SH.techLine({ codec: "flac", bits: 16, sample_rate: 44100, bitrate: 912 }), "FLAC · 16-bit · 44.1 kHz · 912 kbps");
  assert.equal(SH.techLine({ codec: "mp4a", bits: 16, sample_rate: 44100, bitrate: 97 }), "AAC · 44.1 kHz · 97 kbps"); // no bit depth for lossy
  assert.equal(SH.techLine({}), "");
  assert.equal(SH.scorePct(0.934), "93%");
  assert.equal(SH.scorePct(null), "");
});

// ---- constants at their boundaries, inputs never mutated ----
test("row swipes: the commit point, the band a flick must uncover, the flick speed", () => {
  assert.equal(SH.ROW_COMMIT, 115);
  assert.equal(SH.ROW_BAND, 60);
  assert.equal(SH.ROW_FLICK, 900);
  assert.equal(SH.commitAt(400), 115);
  assert.equal(SH.commitAt(200), 90); // at most 45% of a narrow row
  assert.equal(SH.commitAt(0), 115);
  assert.equal(SH.commitAt(Infinity), 115);
  assert.equal(SH.rowCommit(114, 0, 400), 0);
  assert.equal(SH.rowCommit(115, 0, 400), 1);
  assert.equal(SH.rowCommit(-115, 0, 400), -1);
  assert.equal(SH.rowCommit(SH.ROW_BAND, SH.ROW_FLICK, 400), 1);
  assert.equal(SH.rowCommit(SH.ROW_BAND - 0.1, 5000, 400), 0);
  assert.equal(SH.rowCommit(SH.ROW_BAND, SH.ROW_FLICK - 1, 400), 0);
  assert.equal(SH.rowCommit(SH.ROW_BAND, -SH.ROW_FLICK, 400), 0); // a flick back the other way
  assert.equal(SH.rowCommit(-SH.ROW_BAND, -SH.ROW_FLICK, 400), -1);
  assert.equal(SH.rowOffset(-50, 400), -50);
  assert.ok(Math.abs(SH.rowOffset(200, 400) - (115 + 85 / 3)) < 1e-9);
});

test("swipe prefs: known actions only, defaults otherwise", () => {
  assert.deepStrictEqual(SH.SWIPE_ACTIONS, ["next", "queue", "like", "keep", "off"]);
  assert.deepStrictEqual(SH.SWIPE_DEFAULT, { right: "next", left: "queue" });
  const raw = { right: "like", left: "bogus" }, s = snapshot(raw);
  assert.deepStrictEqual(SH.swipePrefs(raw), { right: "like", left: "queue" });
  s.same(assert);
  assert.deepStrictEqual(SH.swipePrefs(null), SH.SWIPE_DEFAULT);
  assert.deepStrictEqual(SH.swipePrefs("x"), SH.SWIPE_DEFAULT);
  assert.deepStrictEqual(SH.swipePrefs({ right: "off", left: "keep" }), { right: "off", left: "keep" });
  assert.equal(SH.swipeAction(SH.SWIPE_DEFAULT, 0), "off");
  assert.equal(SH.swipeUsable("off", {}), false);
  assert.equal(SH.swipeUsable("keep", { keep: "none", canKeep: true }), true);
  assert.equal(SH.swipeUsable("keep", { keep: "kept", canKeep: true }), false);
  assert.equal(SH.swipeUsable("keep", { keep: "none" }), false);
});

test("sleep timer: presets, +5 min near the 12 h cap, steppers and clock at their edges", () => {
  assert.deepStrictEqual(SH.SLEEP_PRESETS, [15, 30, 45, 60]);
  assert.equal(SH.SLEEP_MAX, 720);
  const now = 1_000_000, min = 60000;
  assert.deepStrictEqual(SH.extend(now + 700 * min, 700, now), { deadline: now + 705 * min, total: 705 });
  assert.deepStrictEqual(SH.extend(now + 719 * min, 719, now), { deadline: now + 720 * min, total: 720 });
  assert.deepStrictEqual(SH.extend(now + 720 * min, 720, now), { deadline: now + 720 * min, total: 720 });
  assert.deepStrictEqual(SH.extend(now - 5000, 30, now), { deadline: now + 5 * min, total: 30 }); // already due: from now
  assert.deepStrictEqual(SH.stepCustom({ h: 11, m: 55 }, "h", 1), { h: 12, m: 0 });
  assert.deepStrictEqual(SH.stepCustom({ h: 12, m: 0 }, "m", 1), { h: 12, m: 0 });
  assert.deepStrictEqual(SH.stepCustom({ h: 0, m: 0 }, "m", -1), { h: 0, m: 55 });
  assert.deepStrictEqual(SH.stepCustom({ h: 0, m: 0 }, "h", -1), { h: 0, m: 0 });
  assert.deepStrictEqual(SH.stepCustom(null, "m", 1), { h: 0, m: 5 });
  assert.equal(SH.customMinutes({ h: 12, m: 30 }), 720);
  assert.equal(SH.customMinutes({ h: -1 }), 0);
  assert.deepStrictEqual(SH.clockParts(-5), { h: "00", m: "00", s: "00" });
  assert.deepStrictEqual(SH.clockParts(1), { h: "00", m: "00", s: "01" }); // rounds up: never shows 0 while running
  assert.deepStrictEqual(SH.clockParts(12 * 3600000), { h: "12", m: "00", s: "00" });
  assert.equal(SH.sleepFrac(30 * min, 30), 1);
  assert.equal(SH.sleepFrac(-1, 30), 0);
  assert.equal(SH.sleepFrac(1000, 0), 0);
});

test("the track menu with no context, and its rules never mutate the context", () => {
  assert.deepStrictEqual(SH.menuModel(), { head: ["share", "info"], quick: ["next", "queue", "playlist", "like"], goto: [],
    recs: ["dislike"], more: ["info"] });
  const ctxs = [{ placeholder: true, matched: true, context: "queue" }, { src: "yt", playing: true, context: "player", keep: "kept", artists: 2, album: true, hide: true },
    { src: "nd", context: "list", keep: "kept" }];
  const s = snapshot(ctxs);
  for (const c of ctxs) { SH.menuModel(c); SH.canFix(c); SH.swipeUsable("keep", c); }
  s.same(assert);
  assert.equal(SH.fixKey({ key: "", mkey: "m1" }), "m1");
  assert.equal(SH.fixKey({ key: 5 }), null);
  assert.equal(SH.fixKey(null), null);
});
