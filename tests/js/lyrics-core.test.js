"use strict";
// lyrics-core (LY): LRC parsing, word timing from /api/lyrics `words`, the per-track timing offset.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { load, snapshot } = require("./harness");

const LY = load("lyrics-core");
const ms = (lines) => lines.map((l) => [Math.round(l.t * 1000), l.text]);

test("parseLRC: several stamps per line, metadata skipped, word tags stripped, [offset:] after the lines, sorted", () => {
  const lines = LY.parseLRC("[ar:X]\n[00:12.00]<00:12.00>b <00:12.50>c\n[00:01.5][00:20.123]a\n[00:05:20]\n[offset:+500]");
  assert.deepStrictEqual(ms(lines), [[1000, "a"], [4700, ""], [11500, "b c"], [19623, "a"]]);
});

test("parseLRC: fraction widths, minutes past 59, CRLF, spaces squeezed, junk lines", () => {
  const lines = LY.parseLRC("[01:02.3]x\r\n[00:00.05]  two   words \r\n[100:00]late\nno stamp\n[xx:10]bad\n[ti:Title]");
  assert.deepStrictEqual(ms(lines), [[50, "two words"], [62300, "x"], [6000000, "late"]]);
  assert.deepStrictEqual(LY.parseLRC(""), []);
  assert.deepStrictEqual(LY.parseLRC(null), []);
  assert.deepStrictEqual(LY.parseLRC(undefined), []);
});

test("parseLRC: a negative [offset:] delays the lines, a big positive one clamps at 0", () => {
  assert.deepStrictEqual(ms(LY.parseLRC("[offset:-250]\n[00:01.00]a")), [[1250, "a"]]);
  assert.deepStrictEqual(ms(LY.parseLRC("[00:01.00]a\n[00:09.00]b\n[OFFSET: +5000 ]")), [[0, "a"], [4000, "b"]]);
});

test("attachWords: one entry per line or nothing; junk words dropped; sorted; inputs untouched", () => {
  const L = LY.parseLRC("[00:01.00]one two\n[00:05.00]\n[00:09.00]three");
  const W = [[{ t: 1.4, text: "two" }, { t: 1, d: 0.4, text: "one " }, { t: "x", text: "bad" }, { t: 2, text: "" }, null], [], [{ t: 9, text: "three" }]];
  const s = snapshot(L, W);
  const A = LY.attachWords(L, W);
  assert.deepStrictEqual(A[0].words, [{ t: 1, d: 0.4, text: "one " }, { t: 1.4, d: null, text: "two" }]);
  assert.equal(A[0].text, "one two");
  assert.equal(A[1], L[1]); // no usable words: the line itself
  assert.equal(A[2].words.length, 1);
  s.same(assert);
  assert.equal(LY.attachWords(L, W.slice(0, 2)), L); // misaligned: line mode
  assert.equal(LY.attachWords(L, null), L);
  assert.equal(LY.attachWords(L, "words"), L);
  assert.deepStrictEqual(LY.attachWords(L, [null, "x", 5]), L); // entries that aren't lists carry no words
});

test("indexAt / wordAt: the last entry at or before now", () => {
  assert.equal(LY.indexAt([1, 5, 9], 0.5), -1);
  assert.equal(LY.indexAt([1, 5, 9], 1), 0);
  assert.equal(LY.indexAt([1, 5, 9], 5), 1);
  assert.equal(LY.indexAt([1, 5, 9], 8.999), 1);
  assert.equal(LY.indexAt([1, 5, 9], 100), 2);
  assert.equal(LY.indexAt([], 3), -1);
  assert.equal(LY.indexAt([2, 2, 2], 2), 2); // equal stamps: the last one
  const words = [{ t: 1, text: "a" }, { t: 1.4, text: "b" }];
  assert.equal(LY.wordAt(words, 1.2), 0);
  assert.equal(LY.wordAt(words, 1.4), 1);
  assert.equal(LY.wordAt(words, 0.9), -1);
  assert.equal(LY.wordAt(undefined, 3), -1);
});

test("indexAt agrees with a linear scan on generated sorted lists", () => {
  let seed = 7;
  const rand = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648);
  for (let k = 0; k < 200; k++) {
    const times = Array.from({ length: Math.floor(rand() * 12) }, () => Math.round(rand() * 20) / 2).sort((a, b) => a - b);
    const now = Math.round(rand() * 24 - 2) / 2;
    let want = -1;
    times.forEach((x, i) => { if (x <= now) want = i; });
    assert.equal(LY.indexAt(times, now), want, `${times} @ ${now}`);
  }
});

test("offset: + = the lyrics come later; seeking to a line lands where it's shown", () => {
  assert.equal(LY.lyricTime(10, 500), 9.5);
  assert.equal(LY.lyricTime(10, -1500), 11.5);
  assert.equal(LY.lyricTime(10, undefined), 10);
  assert.equal(LY.lyricTime(10, "junk"), 10);
  assert.equal(LY.seekTime(9.5, 500), 10);
  assert.equal(LY.seekTime(0.2, -1000), 0); // never before the start
  for (const off of [-2500, 0, 700]) assert.equal(LY.indexAt([1, 5, 9], LY.lyricTime(LY.seekTime(5, off), off)), 1);
});

test("nudge: rounded, clamped to ±OFFSET_MAX", () => {
  assert.equal(LY.OFFSET_MAX, 10000);
  assert.equal(LY.NUDGE, 500);
  assert.equal(LY.nudge(0, 500), 500);
  assert.equal(LY.nudge(9800, 500), 10000);
  assert.equal(LY.nudge(-9800, -500), -10000);
  assert.equal(LY.nudge(undefined, -500), -500);
  assert.equal(LY.nudge(12.4, 0), 12);
  assert.equal(LY.nudge(50000, 0), 10000); // an out-of-range stored value comes back in range
});

test("fmtOffset and sourceLabel", () => {
  assert.equal(LY.fmtOffset(0), "0 s");
  assert.equal(LY.fmtOffset(500), "+0.5 s");
  assert.equal(LY.fmtOffset(-1500), "−1.5 s");
  assert.equal(LY.fmtOffset(20), "0 s");
  assert.equal(LY.fmtOffset(10000), "+10.0 s");
  assert.equal(LY.fmtOffset(null), "0 s");
  assert.equal(LY.sourceLabel("lrclib-pinned"), "LRCLIB (your pick)");
  assert.equal(LY.sourceLabel("navidrome"), "your library");
  assert.equal(LY.sourceLabel("other"), "other");
  assert.equal(LY.sourceLabel(undefined), "");
});
