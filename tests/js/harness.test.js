"use strict";
// The loader itself: it finds exactly the cores static/app.js declares, and fails loudly on broken markers.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { CORES, load, declared, slice } = require("./harness");

const fake = (body, name = "queue-core") => `x\n  // ==== ${name}:start ====\n${body}\n  // ==== ${name}:end ====\ny\n`;

test("static/app.js declares exactly the ten cores these tests load, each once", () => {
  assert.deepStrictEqual(declared().slice().sort(), Object.keys(CORES).sort());
});

test("every core loads to an object of functions and constants", () => {
  for (const name of Object.keys(CORES)) {
    const core = load(name);
    const fns = Object.values(core).filter((v) => typeof v === "function");
    assert.ok(fns.length >= 4, `${name}: ${fns.length} functions`);
  }
});

test("a missing, duplicated or reversed marker fails with the marker named", () => {
  assert.throws(() => load("queue-core", { src: "const Q = 1;\n", file: "app.js" }),
    /app\.js: expected exactly one "\/\/ ==== queue-core:start ====" and one "\/\/ ==== queue-core:end ====" line, found 0 and 0/);
  assert.throws(() => load("queue-core", { src: fake("const Q = 1;") + fake("const Q = 2;") }), /found 2 and 2/);
  assert.throws(() => load("queue-core", { src: "// ==== queue-core:start ====\nconst Q = 1;\n" }), /found 1 and 0/);
  assert.throws(() => load("queue-core", { src: "// ==== queue-core:end ====\nconst Q = 1;\n// ==== queue-core:start ====\n" }),
    /comes before its start marker/);
  // a marker mentioned inside other text isn't a marker line
  assert.throws(() => load("queue-core", { src: "// see // ==== queue-core:start ==== below\n" }), /found 0 and 0/);
});

test("the slice must declare its core; unknown names are refused", () => {
  assert.throws(() => load("queue-core", { src: fake("const R = (() => ({}))();") }), /must start with "const Q = "/);
  assert.throws(() => load("nope-core"), /unknown core "nope-core"/);
});

test("the slice is the text between the markers, run in strict mode, with app.js line numbers", () => {
  const src = fake("  const Q = (() => {\n    return { f: () => { undeclared = 1; } };\n  })();");
  assert.deepStrictEqual(slice(src, "queue-core").line, 3);
  const Q = load("queue-core", { src, file: "fake-app.js" });
  assert.throws(() => Q.f(), (e) => e instanceof ReferenceError && /fake-app\.js:4/.test(e.stack));
});
