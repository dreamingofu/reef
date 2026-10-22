"use strict";
// palette-core (P): cover colours from synthetic RGBA pixels, contrast, the bounded palette cache.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { load, snapshot } = require("./harness");

const P = load("palette-core");

// node check for the palette-core section of static/app.js (sliced between its sentinels), on synthetic pixels.


// An RGBA image from [[hex, count], ...] (optionally with alpha), in a deterministic shuffled order.
function img(parts, alpha = 255) {
  const px = [];
  for (const [h, count, al = alpha] of parts) {
    const [r, g, b] = P.fromHex(h);
    for (let i = 0; i < count; i++) px.push([r, g, b, al]);
  }
  let seed = 7;
  for (let i = px.length - 1; i > 0; i--) { seed = (seed * 16807) % 2147483647; const j = seed % (i + 1); [px[i], px[j]] = [px[j], px[i]]; }
  return Uint8ClampedArray.from(px.flat());
}
// Jitter a colour a little per pixel, like a real photo.
function noisy(hex, count, amp = 10) {
  const [r, g, b] = P.fromHex(hex);
  const out = [];
  let seed = 11;
  for (let i = 0; i < count; i++) {
    seed = (seed * 48271) % 2147483647;
    const d = (seed % (2 * amp + 1)) - amp;
    out.push([P.hex([r + d, g - d, b + (d >> 1)].map((v) => Math.max(0, Math.min(255, v)))), 1]);
  }
  return out;
}
const hsl = (h) => P.rgbToHsl(...P.fromHex(h));
const hueDist = (x, y) => Math.min(Math.abs(x - y), 360 - Math.abs(x - y));
const roles = (p) => {
  const base = P.fromHex(p.base), acc = P.fromHex(p.accent), on = P.fromHex(p.onAccent);
  assert.ok(base && acc && on, "hex roles");
  assert.ok(P.contrast(acc, base) >= P.MIN_CONTRAST, `accent ${p.accent} on base ${p.base}: ${P.contrast(acc, base).toFixed(2)}`);
  assert.ok(P.contrast(on, acc) >= P.MIN_CONTRAST, `on-accent ${p.onAccent} on ${p.accent}: ${P.contrast(on, acc).toFixed(2)}`);
  assert.ok(hsl(p.base)[2] <= 0.265, `base lightness ${hsl(p.base)[2]}`);
  return { base: hsl(p.base), accent: hsl(p.accent), on: hsl(p.onAccent) };
};

test("colour helpers round-trip", () => {
  for (const h of ["#fb2c5a", "#000000", "#ffffff", "#1db954", "#3a7bd5", "#808080"]) {
    const [hh, s, l] = P.rgbToHsl(...P.fromHex(h));
    assert.equal(P.hex(P.hslToRgb(hh, s, l)), h);
  }
  assert.equal(P.contrast([0, 0, 0], [255, 255, 255]).toFixed(1), "21.0");
  assert.equal(P.contrast([10, 10, 10], [10, 10, 10]), 1);
  assert.equal(P.fromHex("nope"), null);
});

test("empty / fully transparent image -> null", () => {
  assert.equal(P.derive(new Uint8ClampedArray(0)), null);
  assert.equal(P.derive(img([["#ff0000", 50, 0]])), null);
  assert.equal(P.derive(null), null);
});

test("solid red cover: red base (dark), red accent (light), readable roles", () => {
  const p = P.derive(img([["#c0392b", 4096]]));
  const r = roles(p);
  assert.equal(p.grey, false);
  assert.ok(hueDist(r.base[0], hsl("#c0392b")[0]) < 8 && hueDist(r.accent[0], hsl("#c0392b")[0]) < 8);
  assert.ok(r.accent[2] >= 0.78, `accent lightness ${r.accent[2]}`);
  assert.ok(r.on[2] <= 0.25);
});

test("grey cover (B/W photo): neutral roles, grey flag", () => {
  const p = P.derive(img([["#202020", 1500], ["#808080", 1500], ["#e0e0e0", 1000], ["#848080", 96]]));
  const r = roles(p);
  assert.equal(p.grey, true);
  assert.ok(r.base[1] < 0.02 && r.accent[1] < 0.02, "no saturation");
});

test("dark muted brown cover with a small vivid cyan: base brown, accent cyan", () => {
  const p = P.derive(img([["#3b2f2a", 3200], ["#00c8ff", 700], ["#f0e8e0", 196]]));
  const r = roles(p);
  assert.equal(p.grey, false);
  assert.ok(hueDist(r.base[0], hsl("#3b2f2a")[0]) < 15, `base hue ${r.base[0]}`);
  assert.ok(hueDist(r.accent[0], hsl("#00c8ff")[0]) < 15, `accent hue ${r.accent[0]}`);
});

test("vivid colours outrank a slightly bigger dull one (x1.3), and win the accent", () => {
  const data = img([["#5a6b5a", 1800], ["#d62839", 1500], ["#101010", 796]]);
  const B = P.buckets(data);
  assert.equal(B.list[0].rgb.join(), "214,40,57"); // 1500 x 1.3 > 1800
  assert.ok(B.list[0].vivid && !B.list[1].vivid);
  const r = roles(P.derive(data));
  assert.ok(hueDist(r.accent[0], hsl("#d62839")[0]) < 15, `accent hue ${r.accent[0]}`);
});

test("a bright saturated colour (brightness > .9) still makes the accent", () => {
  const r = roles(P.derive(img([["#2b2d42", 3000], ["#ff1744", 1096]])));
  assert.ok(hueDist(r.accent[0], hsl("#ff1744")[0]) < 15, `accent hue ${r.accent[0]}`);
});

test("mostly black cover keeps a dark base and still finds the colour", () => {
  const p = P.derive(img([["#000000", 3500], ["#ffd400", 400], ["#ffffff", 196]]));
  const r = roles(p);
  assert.ok(r.base[2] <= 0.265 && r.base[2] >= 0.075, `base L ${r.base[2]}`);
  assert.ok(hueDist(r.accent[0], hsl("#ffd400")[0]) < 12, `accent hue ${r.accent[0]}`);
});

test("bright pastel cover: base is still clamped dark, contrast holds", () => {
  const p = P.derive(img([["#ffd6e0", 3000], ["#c1e1ff", 1096]]));
  roles(p);
});

test("noisy photo-like pixels bucket together (population ranking is stable)", () => {
  const p = P.derive(img([...noisy("#1e3a8a", 3000, 12), ...noisy("#f97316", 1000, 12)]));
  const r = roles(p);
  assert.ok(hueDist(r.base[0], hsl("#1e3a8a")[0]) < 20, `base hue ${r.base[0]}`);
});

test("buckets: transparent pixels skipped, shares add up, best first", () => {
  const B = P.buckets(img([["#ff0000", 30], ["#0000ff", 10], ["#00ff00", 60, 10]]));
  assert.equal(B.total, 40);
  assert.equal(B.list[0].rgb.join(), "255,0,0");
  assert.ok(Math.abs(B.list.reduce((x, c) => x + c.share, 0) - 1) < 1e-9);
  assert.ok(B.list.every((c) => c.score >= c.n));
});

test("deterministic", () => {
  const data = img([["#5a189a", 2000], ["#ff6d00", 900], ["#240046", 1196]]);
  assert.deepEqual(P.derive(data), P.derive(data));
});

test("vars: CSS properties, DEFAULT for junk", () => {
  assert.deepEqual(P.vars({ base: "#101010", accent: "#ffcc00", onAccent: "#332200" }),
    { "--c-base": "#101010", "--c-accent": "#ffcc00", "--c-on-accent": "#332200" });
  assert.deepEqual(P.vars(null), P.vars(P.DEFAULT));
  assert.deepEqual(P.vars({ base: "red", accent: "#fff", onAccent: "#000" }), P.vars(P.DEFAULT));
  assert.equal(P.vars(P.BLACK)["--c-base"], "#000000");
});

test("remember: bounded, newest last, input not mutated", () => {
  let c = {};
  for (let i = 0; i < 10; i++) c = P.remember(c, "id" + i, { i }, 4);
  assert.deepEqual(Object.keys(c), ["id6", "id7", "id8", "id9"]);
  const before = JSON.stringify(c);
  const d = P.remember(c, "id7", { i: 70 }, 4);
  assert.equal(JSON.stringify(c), before);
  assert.deepEqual(Object.keys(d), ["id6", "id8", "id9", "id7"]);
  assert.deepEqual(d.id7, { i: 70 });
  assert.deepEqual(Object.keys(P.remember(null, "x", 1, 4)), ["x"]);
});

test("remember: max <= 0 keeps nothing (the bound holds, the new entry too)", () => {
  assert.deepEqual(P.remember({ a: 1, b: 2 }, "x", 1, 0), {});
  assert.deepEqual(P.remember({ a: 1 }, "x", 1, -3), {});
  assert.deepEqual(P.remember({ a: 1 }, "x", 1, NaN), {});
  assert.deepEqual(Object.keys(P.remember({ a: 1, b: 2 }, "x", 1, 1)), ["x"]);
});

test("an all-black cover is grey with a dark neutral base", () => {
  const p = P.derive(img([["#050505", 3000], ["#0a0a0a", 1096]]));
  const r = roles(p);
  assert.equal(p.grey, true);
  assert.ok(r.base[1] < 0.02 && r.base[2] >= 0.075);
});

// ---- WCAG maths, derivation invariants on generated covers, inputs never mutated ----
test("luminance and contrast: the WCAG endpoints, symmetric", () => {
  assert.equal(P.luminance([0, 0, 0]), 0);
  assert.ok(Math.abs(P.luminance([255, 255, 255]) - 1) < 1e-12);
  assert.ok(Math.abs(P.contrast([255, 255, 255], [0, 0, 0]) - 21) < 1e-9);
  assert.equal(P.contrast([10, 200, 30], [200, 10, 90]), P.contrast([200, 10, 90], [10, 200, 30]));
  assert.equal(P.contrast([90, 90, 90], [90, 90, 90]), 1);
  assert.ok(P.luminance([0, 255, 0]) > P.luminance([255, 0, 0]) && P.luminance([255, 0, 0]) > P.luminance([0, 0, 255]));
  assert.equal(P.MIN_CONTRAST, 4.5);
});

test("fromHex / hex: strict 6-digit form, # optional, any case", () => {
  assert.deepStrictEqual(P.fromHex("#ABCDEF"), [171, 205, 239]);
  assert.deepStrictEqual(P.fromHex("abcdef"), [171, 205, 239]);
  for (const bad of ["#fff", "#abcdeg", "", null, undefined, "#1234567", " #123456"]) assert.equal(P.fromHex(bad), null, String(bad));
  assert.equal(P.hex([0, 15.6, 255]), "#0010ff");
});

test("derive on 60 seeded random covers: valid roles, a dark base, readable accent and on-accent", () => {
  let seed = 2026;
  const rand = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648);
  for (let k = 0; k < 60; k++) {
    const colours = Array.from({ length: 1 + Math.floor(rand() * 5) }, () => [0, 0, 0].map(() => Math.floor(rand() * 256)));
    const data = new Uint8ClampedArray(64 * 64 * 4);
    for (let i = 0; i < data.length; i += 4) {
      const c = colours[Math.floor(rand() * colours.length)];
      data.set([c[0], c[1], c[2], rand() < 0.05 ? 0 : 255], i);
    }
    const s = snapshot(data);
    const p = P.derive(data);
    s.same(assert);
    const what = `cover ${k} ${JSON.stringify(colours)} -> ${JSON.stringify(p)}`;
    for (const role of ["base", "accent", "onAccent"]) assert.match(p[role], /^#[0-9a-f]{6}$/, what);
    assert.equal(typeof p.grey, "boolean");
    const [, , l] = P.rgbToHsl(...P.fromHex(p.base));
    assert.ok(l <= 0.27, `base too light: ${what}`);
    assert.ok(P.contrast(P.fromHex(p.accent), P.fromHex(p.base)) >= P.MIN_CONTRAST, `accent unreadable: ${what}`);
    assert.ok(P.contrast(P.fromHex(p.onAccent), P.fromHex(p.accent)) >= P.MIN_CONTRAST, `on-accent unreadable: ${what}`);
    assert.deepStrictEqual(P.derive(data), p, `not deterministic: ${what}`);
  }
});

test("vars and remember leave their inputs alone", () => {
  const p = { base: "#101010", accent: "#ff8899", onAccent: "#221111", grey: false };
  const cache = { a: P.DEFAULT, b: P.BLACK };
  const s = snapshot(p, cache);
  assert.deepStrictEqual(P.vars(p), { "--c-base": "#101010", "--c-accent": "#ff8899", "--c-on-accent": "#221111" });
  assert.deepStrictEqual(P.vars({ ...p, accent: "red" }), P.vars(P.DEFAULT));
  assert.deepStrictEqual(P.vars(null), P.vars(P.DEFAULT));
  assert.deepStrictEqual(Object.keys(P.remember(cache, "a", p)), ["b", "a"]); // re-remembered: moves to the end
  assert.deepStrictEqual(Object.keys(P.remember(cache, "c", p, 2)), ["b", "c"]);
  assert.deepStrictEqual(Object.keys(P.remember(null, "c", p)), ["c"]);
  s.same(assert);
  assert.ok(Object.isFrozen(P.DEFAULT) && Object.isFrozen(P.BLACK));
});
