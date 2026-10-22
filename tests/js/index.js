"use strict";
// Entry point for `node --test tests/js/`: newer Node resolves a directory argument to this file, so it loads every
// *.test.js here into the one test run. (Node versions that search a directory for test files run them directly and
// never pick up this file: it doesn't match their test file patterns.)
const fs = require("node:fs");
const path = require("node:path");

for (const f of fs.readdirSync(__dirname).filter((f) => f.endsWith(".test.js")).sort()) require(path.join(__dirname, f));
