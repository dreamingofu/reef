"use strict";
// lyrics-view-core (LV): rows and gaps, active word / fill, depth, scroll anchor, long-line shrink.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { load, snapshot } = require("./harness");

const LV = load("lyrics-view-core");
const LY = load("lyrics-core");

// node check for the lyrics-view-core section of static/app.js (sliced between its sentinels).
// lyrics-core too, to feed rows() what the app feeds it

const near = (x, y, eps = 1e-6) => assert.ok(Math.abs(x - y) <= eps, `${x} != ${y}`);
const kinds = (rs) => rs.map((r) => (r.kind === "gap" ? `gap ${r.t}-${r.end}` : `${r.t} ${r.text}`));

test("isBreak: blanks and note-only lines are breaks, words aren't", () => {
  for (const t of ["", "  ", "♪", "♫ ♫", "…", null, undefined]) assert.equal(LV.isBreak(t), true, String(t));
  for (const t of ["a", "♪ la la", "1", "¿"]) assert.equal(LV.isBreak(t), false, t);
});

test("indexAt: binary search, -1 before the first", () => {
  assert.equal(LV.indexAt([1, 5, 9], 0.5), -1);
  assert.equal(LV.indexAt([1, 5, 9], 5), 1);
  assert.equal(LV.indexAt([1, 5, 9], 100), 2);
  assert.equal(LV.indexAt([], 3), -1);
});

test("rows: intro gap, blank-line gaps >= 4 s, short blanks dropped, trailing blank ignored", () => {
  const lines = LY.parseLRC("[00:06.00]one\n[00:08.00]two\n[00:10.00]\n[00:11.00]three\n[00:14.00]\n[00:25.00]four\n[00:30.00]");
  assert.deepEqual(kinds(LV.rows(lines)), ["gap 0-6", "6 one", "8 two", "11 three", "gap 14-25", "25 four"]);
  assert.equal(LV.rows(lines)[1].src, 0);
});

test("rows: no intro gap when the first line starts within 4 s; a first blank becomes a gap from 0", () => {
  assert.deepEqual(kinds(LV.rows(LY.parseLRC("[00:03.50]a\n[00:05.00]b"))), ["3.5 a", "5 b"]);
  assert.deepEqual(kinds(LV.rows(LY.parseLRC("[00:01.00]\n[00:09.00]a"))), ["gap 0-9", "9 a"]);
  assert.deepEqual(kinds(LV.rows(LY.parseLRC("[00:00.00]♪\n[00:02.00]a"))), ["2 a"]); // too short a break
});

test("rows: consecutive blanks merge into one gap; ♪ lines are breaks", () => {
  const rs = LV.rows(LY.parseLRC("[00:01.00]a\n[00:05.00]\n[00:09.00]♪\n[00:15.00]b"));
  assert.deepEqual(kinds(rs), ["1 a", "gap 5-15", "15 b"]);
});

test("rows: word timing -> the silence after a line's last word is a gap when >= 4 s", () => {
  const lines = LY.attachWords(LY.parseLRC("[00:01.00]a b\n[00:10.00]c\n[00:12.00]d"), [
    [{ t: 1, d: 0.5, text: "a " }, { t: 1.5, d: 0.5, text: "b" }], // ends at 2, next at 10 -> gap 2-10
    [{ t: 10, d: 0.5, text: "c" }], // ends 10.5, next 12 -> no gap
    [{ t: 12, text: "d" }], // no duration: no end known
  ]);
  const rs = LV.rows(lines);
  assert.deepEqual(kinds(rs), ["1 a b", "gap 2-10", "10 c", "12 d"]);
  assert.equal(rs[0].words.length, 2);
  assert.equal(rs[3].words.length, 1);
  assert.equal(LV.lineEnd(rs[0].words), 2);
  assert.equal(LV.lineEnd(rs[3].words), null);
  assert.equal(LV.lineEnd(null), null);
});

test("rows: junk in, sane out", () => {
  assert.deepEqual(LV.rows(null), []);
  assert.deepEqual(LV.rows([{ t: NaN, text: "x" }, null]), []);
});

test("wordAt / wordFill: own duration, else until the next word, else the line end, else .5 s", () => {
  const w = [{ t: 1, d: 0.4, text: "a" }, { t: 2, d: null, text: "b" }, { t: 3, d: null, text: "c" }];
  assert.equal(LV.wordAt(w, 0.5), -1);
  assert.equal(LV.wordAt(w, 2.5), 1);
  assert.equal(LV.wordAt(w, 9), 2);
  assert.equal(LV.wordAt(null, 1), -1);
  near(LV.wordFill(w, 0, 1.2), 0.5);
  near(LV.wordFill(w, 0, 0.5), 0);
  near(LV.wordFill(w, 0, 5), 1);
  near(LV.wordFill(w, 1, 2.25), 0.25); // until the next word (1 s)
  near(LV.wordFill(w, 2, 3.5, 5), 0.25); // until the line's end (2 s)
  near(LV.wordFill(w, 2, 3.25), 0.5); // no end known: half a second
  assert.equal(LV.wordFill(w, 7, 3), 0);
});

test("glows: words held more than 1.1 s", () => {
  assert.equal(LV.glows({ t: 1, d: 1.2 }), true);
  assert.equal(LV.glows({ t: 1, d: 1 }), false);
  assert.equal(LV.glows({ t: 1, d: null }, { t: 3 }), true);
  assert.equal(LV.glows({ t: 1, d: null }), false);
});

test("gapProgress / dotFill: three dots fill in turn, fading .65 s before the next line", () => {
  const g = { t: 10, end: 16.65 }; // 6 s of filling
  assert.deepEqual(LV.gapProgress(g, 10), { p: 0, out: false });
  near(LV.gapProgress(g, 13).p, 0.5);
  assert.equal(LV.gapProgress(g, 15.9).out, false);
  assert.equal(LV.gapProgress(g, 16).out, true);
  near(LV.gapProgress(g, 30).p, 1);
  const p = LV.gapProgress(g, 13).p; // halfway: first dot full, second half, third empty
  near(LV.dotFill(p, 0), 1); near(LV.dotFill(p, 1), 0.5); near(LV.dotFill(p, 2), 0);
  assert.deepEqual(LV.gapProgress(null, 3), { p: 0, out: false });
});

test("depth: d0..d4 by distance, d5 for 5..8, d9 beyond, past above the active row, nothing active yet", () => {
  assert.equal(LV.depth(5, 5), "d0");
  assert.equal(LV.depth(6, 5), "d1");
  assert.equal(LV.depth(4, 5), "d1 past");
  assert.equal(LV.depth(2, 5), "d3 past");
  assert.equal(LV.depth(9, 5), "d4");
  assert.equal(LV.depth(10, 5), "d5");
  assert.equal(LV.depth(13, 5), "d5");
  assert.equal(LV.depth(14, 5), "d9");
  assert.equal(LV.depth(0, 5), "d5 past");
  assert.equal(LV.depth(30, 15), "d9");
  assert.equal(LV.depth(0, 15), "d9 past");
  assert.equal(LV.depth(0, -1), "d1");
  assert.equal(LV.depth(3, -1), "d4");
  assert.equal(LV.depth(4, -1), "d5");
  assert.equal(LV.depth(8, -1), "d9");
  // levels: blur only ever on d1..d4 (CSS), so at most 8 blurred rows
  assert.deepEqual([0, 1, 2, 3, 4, 5, 8, 9, 50].map(LV.level), [0, 1, 2, 3, 4, 5, 5, 9, 9]);
  let blurred = 0;
  for (let i = 0; i < 100; i++) if (/^d[1-4]\b/.test(LV.depth(i, 50))) blurred++;
  assert.equal(blurred, 8);
});

test("depthRange: covers every row whose class changes between two active rows", () => {
  assert.deepEqual(LV.depthRange(5, 6, 50), [0, 15]);
  assert.deepEqual(LV.depthRange(20, 21, 50), [11, 30]);
  assert.deepEqual(LV.depthRange(-1, 0, 50), [0, 9]);
  assert.deepEqual(LV.depthRange(40, 2, 42), [0, 41]);
  assert.equal(LV.depthRange(0, 0, 0), null);
  // brute force: outside the range the classes are equal
  for (const [x, y] of [[5, 6], [6, 5], [-1, 3], [-1, 12], [0, 30], [30, 0], [12, 12], [20, 21], [21, 20], [3, 60]]) {
    const [lo, hi] = LV.depthRange(x, y, 80);
    for (let i = 0; i < 80; i++) if (i < lo || i > hi) assert.equal(LV.depth(i, x), LV.depth(i, y), `${x}->${y} row ${i}`);
  }
});

test("anchorTop / stagger / glides", () => {
  assert.equal(LV.anchorTop(1000, 600), 790);
  assert.equal(LV.anchorTop(100, 600), 0);
  assert.equal(LV.stagger(3, 5), 0);
  assert.equal(LV.stagger(5, 5), 40);
  assert.equal(LV.stagger(8, 5), 100);
  assert.equal(LV.stagger(50, 5), 200);
  assert.equal(LV.glides(0, 600), false);
  assert.equal(LV.glides(60, 600), true);
  assert.equal(LV.glides(-900, 600), true);
  assert.equal(LV.glides(901, 600), false);
});

test("needsFrames: only an active word-timed line or interlude needs the rAF loop (mixed timing too)", () => {
  // mixed lyrics: line 1 word-timed, line 2 line-timed, a gap, then a word-timed line
  const lines = LY.attachWords(LY.parseLRC("[00:01.00]a b\n[00:03.00]c d\n[00:05.00]\n[00:15.00]e"), [
    [{ t: 1, d: 0.5, text: "a " }, { t: 1.5, d: 0.5, text: "b" }], [], [], [{ t: 15, d: 1, text: "e" }],
  ]);
  const rs = LV.rows(lines);
  assert.deepEqual(kinds(rs), ["1 a b", "3 c d", "gap 5-15", "15 e"]);
  assert.deepEqual(rs.map(LV.needsFrames), [true, false, true, true]);
  // what the app evaluates: the row active at each time, not the track
  const times = rs.map((r) => r.t);
  const at = (now) => LV.needsFrames(rs[LV.indexAt(times, now)]);
  assert.equal(at(1.2), true);  // word-timed line
  assert.equal(at(3.5), false); // line-timed row in word-timed lyrics: timeupdate only
  assert.equal(at(8), true);    // interlude
  assert.equal(at(0.5), false); // nothing active yet
  assert.equal(LV.needsFrames(undefined), false);
  assert.equal(LV.needsFrames({ kind: "line", words: [] }), false);
});

test("sizeFactor: long lines shrink in steps", () => {
  assert.equal(LV.sizeFactor(10), 1);
  assert.equal(LV.sizeFactor(42), 1);
  assert.equal(LV.sizeFactor(43), 0.9);
  assert.equal(LV.sizeFactor(55), 0.8);
  assert.equal(LV.sizeFactor(73), 0.72);
  assert.equal(LV.sizeFactor(93), 0.66);
  assert.equal(LV.sizeFactor(undefined), 1);
});

test("groups: syllables stay together, spaces split words", () => {
  const w = (s) => s.map((text) => ({ t: 0, text }));
  assert.deepEqual(LV.groups(w(["Ne", "ver ", "gon", "na ", "give"])), [{ from: 0, to: 1, space: true }, { from: 2, to: 3, space: true }, { from: 4, to: 4, space: false }]);
  assert.deepEqual(LV.groups(w(["a", " b", "c"])), [{ from: 0, to: 0, space: true }, { from: 1, to: 2, space: false }]);
  assert.deepEqual(LV.groups(w(["solo "])), [{ from: 0, to: 0, space: false }]);
  assert.deepEqual(LV.groups([]), []);
});

// ---- constants and their defaults, inputs never mutated ----
test("GAP_MIN / GAP_FADE: the default break length and the dots' fade", () => {
  assert.equal(LV.GAP_MIN, 4);
  assert.equal(LV.GAP_FADE, 0.65);
  const at = (gap) => LV.rows([{ t: 0.5, text: "a" }, { t: 1, text: "" }, { t: 1 + gap, text: "b" }]).map((r) => r.kind);
  assert.deepStrictEqual(at(LV.GAP_MIN), ["line", "gap", "line"]);
  assert.deepStrictEqual(at(LV.GAP_MIN - 0.01), ["line", "line"]);
  const gap = { kind: "gap", t: 10, end: 20 };
  assert.deepStrictEqual(LV.gapProgress(gap, 20 - LV.GAP_FADE), { p: 1, out: true });
  assert.equal(LV.gapProgress(gap, 19.3).out, false);
  assert.deepStrictEqual(LV.gapProgress(gap, 5), { p: 0, out: false });
  assert.deepStrictEqual(LV.gapProgress(null, 5), { p: 0, out: false });
});

test("STEPS / FAR / ANCHOR / HOLD_MS: depth levels, the scroll anchor, the manual-scroll hold", () => {
  assert.equal(LV.STEPS, 4);
  assert.equal(LV.FAR, 8);
  assert.deepStrictEqual([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map(LV.level), [0, 1, 2, 3, 4, 5, 5, 5, 5, 9, 9]);
  assert.equal(LV.depth(5, 5), "d0");
  assert.equal(LV.depth(3, 5), "d2 past");
  assert.equal(LV.depth(7, 5), "d2");
  assert.equal(LV.depth(0, -1), "d1"); // nothing active yet: row 0 is one step away
  assert.equal(LV.depth(20, 5), "d9");
  assert.equal(LV.ANCHOR, 0.35);
  assert.equal(LV.anchorTop(1000, 800), 720);
  assert.equal(LV.anchorTop(100, 800), 0); // never above the top
  assert.equal(LV.HOLD_MS, 3500);
});

test("rows never mutate the parsed lines or their words, and skip entries without a time", () => {
  const lines = [{ t: 0, text: "" }, { t: 1, text: "" }, { t: 9, text: "x", words: [{ t: 9, d: 0.5, text: "x" }] }, { t: 20, text: "y" },
    { t: NaN, text: "z" }, null];
  const s = snapshot(lines);
  const rows = LV.rows(lines);
  LV.rows(lines, { minGap: 1 });
  s.same(assert);
  assert.deepStrictEqual(rows.map((r) => (r.kind === "gap" ? `gap ${r.t}-${r.end}` : `${r.t} ${r.text}`)), ["gap 0-9", "9 x", "gap 9.5-20", "20 y"]);
  assert.deepStrictEqual(LV.rows(null), []);
  assert.deepStrictEqual(LV.rows([]), []);
});
