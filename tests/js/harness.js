"use strict";
// Loads one pure core of static/app.js for node:test: the exact text between its sentinel comments
// (`// ==== <name>:start ====` / `// ==== <name>:end ====`), compiled with node:vm in strict mode like the app's
// own IIFE. Nothing is copied: the tests run the shipped source, and stack traces point at static/app.js lines.
// The code runs in this realm, so assert.deepStrictEqual compares its objects and arrays like local ones.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const APP = path.join(__dirname, "..", "..", "static", "app.js");

// Sentinel name -> the const the slice declares and the tests get.
const CORES = Object.freeze({
  "queue-core": "Q",
  "recovery-core": "R",
  "library-core": "L",
  "match-core": "M",
  "lyrics-core": "LY",
  "lyrics-view-core": "LV",
  "palette-core": "P",
  "player-core": "PC",
  "sheet-core": "SH",
  "discovery-core": "DC",
});

const marker = (name, edge) => `// ==== ${name}:${edge} ====`;
// 0-based indexes of the lines that are exactly `text` (indentation aside).
const linesOf = (lines, text) => lines.reduce((out, l, i) => (l.trim() === text ? [...out, i] : out), []);

// The slice of `src` between the markers of core `name`: {code, line (1-based line of its first code line)}.
function slice(src, name, file = APP) {
  const lines = String(src).split("\n");
  const start = linesOf(lines, marker(name, "start")), end = linesOf(lines, marker(name, "end"));
  if (start.length !== 1 || end.length !== 1) {
    throw new Error(`${file}: expected exactly one "${marker(name, "start")}" and one "${marker(name, "end")}" line, ` +
      `found ${start.length} and ${end.length}`);
  }
  if (end[0] <= start[0]) throw new Error(`${file}: "${marker(name, "end")}" (line ${end[0] + 1}) comes before its start marker (line ${start[0] + 1})`);
  return { code: lines.slice(start[0] + 1, end[0]).join("\n"), line: start[0] + 2 };
}

// The core object `name` exports. `src` / `file` default to static/app.js (they're for the harness's own tests).
function load(name, { src, file = APP } = {}) {
  const ident = CORES[name];
  if (!ident) throw new Error(`unknown core "${name}" (known: ${Object.keys(CORES).join(", ")})`);
  const { code, line } = slice(src === undefined ? fs.readFileSync(file, "utf8") : src, name, file);
  const first = code.split("\n").find((l) => l.trim()) || "";
  if (!new RegExp(`^\\s*const ${ident} = `).test(first)) {
    throw new Error(`${file}:${line}: the ${name} slice must start with "const ${ident} = ", found ${JSON.stringify(first.trim())}`);
  }
  const wrapped = `(function () { "use strict";\n${code}\nreturn ${ident};\n})`;
  const core = new vm.Script(wrapped, { filename: file, lineOffset: line - 2 }).runInThisContext()();
  if (!core || typeof core !== "object") throw new Error(`${file}: ${name} did not evaluate to an object`);
  return core;
}

// Every sentinel core name static/app.js (or `src`) declares, in source order.
function declared(src = fs.readFileSync(APP, "utf8")) {
  return [...String(src).matchAll(/^\s*\/\/ ==== ([\w-]+):start ====\s*$/gm)].map((m) => m[1]);
}

// The app IIFE's own (two-space indented) declaration `name` as {code, line, kind}: a function from its
// `function name(` / `async function name(` line, or a const / let from its `const name = ` / `let name = ` line, to
// the first later line indented two spaces (its closing line), or that line alone when it's complete (a one-line
// function ending in `}`, a declaration ending in `;` before any `// comment`).
function decl(src, name, file = APP) {
  const lines = String(src).split("\n");
  const id = String(name).replace(/\$/g, "\\$");
  const fn = new RegExp(`^  (?:async )?function ${id}\\(`), v = new RegExp(`^  (const|let) ${id} = `);
  const at = lines.reduce((out, l, i) => (fn.test(l) || v.test(l) ? [...out, i] : out), []);
  if (at.length !== 1) throw new Error(`${file}: expected exactly one top-level declaration of "${name}", found ${at.length}`);
  const i = at[0], first = lines[i], kind = fn.test(first) ? "function" : first.match(v)[1];
  const whole = kind === "function" ? /\}\s*$/.test(first) : /;\s*(\/\/.*)?$/.test(first);
  let j = i;
  if (!whole) {
    for (j = i + 1; j < lines.length && !/^  \S/.test(lines[j]); j++);
    if (j === lines.length) throw new Error(`${file}:${i + 1}: "${name}" never closes`);
  }
  return { code: lines.slice(i, j + 1).join("\n"), line: i + 1, kind };
}

// The shipped declarations `names` (functions, consts, lets) of static/app.js compiled together in one strict scope,
// each at its own line (stack traces name static/app.js lines), over `vars`: the free variables they use (stubs,
// state, counters), declared as lets around them. Anything else they use is this realm's global, or a ReferenceError.
// Returns one object with a getter for every name and var, and a setter for every var and let.
function scope(names, vars = {}, { src, file = APP } = {}) {
  const text = src === undefined ? fs.readFileSync(file, "utf8") : src;
  const ids = Object.keys(vars);
  for (const id of [...names, ...ids]) if (!/^[A-Za-z_$][\w$]*$/.test(id) || id === "__vars") throw new Error(`bad name "${id}"`);
  const both = ids.filter((id) => names.includes(id));
  if (both.length) throw new Error(`declared by app.js and passed as vars: ${both.join(", ")}`);
  const parts = names.map((n) => ({ name: n, ...decl(text, n, file) })).sort((a, b) => a.line - b.line);
  const out = [`(function (__vars) { "use strict";${ids.length ? ` let ${ids.map((id) => `${id} = __vars.${id}`).join(", ")};` : ""}`];
  for (const p of parts) {
    if (p.line <= out.length) throw new Error(`${file}:${p.line}: "${p.name}" overlaps an earlier declaration`);
    while (out.length < p.line - 1) out.push("");
    out.push(...p.code.split("\n"));
  }
  const settable = new Set([...ids, ...parts.filter((p) => p.kind === "let").map((p) => p.name)]);
  const access = [...names, ...ids].map((n) => `get ${n}() { return ${n}; }${settable.has(n) ? `, set ${n}(v) { ${n} = v; }` : ""}`);
  out.push(`return { ${access.join(", ")} };`, "})");
  return new vm.Script(out.join("\n"), { filename: file }).runInThisContext()(vars);
}

// structuredClone snapshot of `inputs`; `same()` asserts they still match it (pure functions never mutate inputs).
function snapshot(...inputs) {
  const before = structuredClone(inputs);
  return { same: (assert) => assert.deepStrictEqual(inputs, before, "inputs were mutated") };
}

module.exports = { APP, CORES, slice, load, declared, snapshot, decl, scope };
