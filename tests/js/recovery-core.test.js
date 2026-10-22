"use strict";
// recovery-core (R): reload backoff, stall limits, probe verdicts, give-up and stop decisions.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { load, snapshot } = require("./harness");

const R = load("recovery-core");

// Extracts the real recovery core from static/app.js (between sentinel comments) and checks it.

test("backoff 1s/2s/4s, capped at 10s", () => {
  assert.deepEqual([1, 2, 3, 4, 5, 9].map(R.backoff), [1000, 2000, 4000, 8000, 10000, 10000]);
});
test("retryable blocks: 3 retries with backoff, then give up", () => {
  for (const block of [null, "NETWORK", "EXPIRED", "BOT_WALL", "RATE_LIMITED", "UNKNOWN"]) {
    assert.deepEqual(R.onFailure({ block, tries: 0 }), { do: "retry", attempt: 1, delay: 1000 });
    assert.deepEqual(R.onFailure({ block, tries: 1 }), { do: "retry", attempt: 2, delay: 2000 });
    assert.deepEqual(R.onFailure({ block, tries: 2 }), { do: "retry", attempt: 3, delay: 4000 });
    assert.deepEqual(R.onFailure({ block, tries: 3 }), { do: "give-up" });
  }
});
test("final blocks skip at once, even on the first failure and even offline", () => {
  for (const block of ["GONE", "REGION", "MEMBERS_OR_PAID", "LOGIN_REQUIRED"]) {
    assert.deepEqual(R.onFailure({ block, tries: 0 }), { do: "give-up" });
    assert.deepEqual(R.onFailure({ block, tries: 0, online: false }), { do: "give-up" });
    assert.ok(R.isFinal(block));
  }
  assert.ok(!R.isFinal("BOT_WALL") && !R.isFinal("EXPIRED") && !R.isFinal(null));
});
test("offline: wait for `online` instead of burning tries", () => {
  assert.deepEqual(R.onFailure({ block: "NETWORK", tries: 0, online: false }), { do: "wait" });
  assert.deepEqual(R.onFailure({ block: "NETWORK", tries: 3, online: false }), { do: "wait" });
  assert.equal(R.onFailure({ block: "NETWORK", tries: 0, online: undefined }).do, "retry", "unknown onLine = online");
});
test("an early stall (no metadata yet) on a stream that answers keeps waiting; a dead one still retries", () => {
  assert.deepEqual(R.onFailure({ block: null, tries: 0, early: true }), { do: "keep" });
  assert.deepEqual(R.onFailure({ block: null, tries: 3, early: true }), { do: "keep" });
  assert.deepEqual(R.onFailure({ block: "NETWORK", tries: 0, early: true }), { do: "retry", attempt: 1, delay: 1000 });
  assert.deepEqual(R.onFailure({ block: "GONE", tries: 0, early: true }), { do: "give-up" });
  assert.deepEqual(R.onFailure({ block: null, tries: 0, early: false }), { do: "retry", attempt: 1, delay: 1000 });
});
test("probeVerdict", () => {
  assert.deepEqual(R.probeVerdict(206, null), { ok: true, block: null, hint: "" });
  assert.deepEqual(R.probeVerdict(200, null), { ok: true, block: null, hint: "" });
  assert.deepEqual(R.probeVerdict(0, null), { ok: false, block: "NETWORK", hint: "" });
  assert.deepEqual(R.probeVerdict(404, null), { ok: false, block: "GONE", hint: "" });
  assert.deepEqual(R.probeVerdict(502, { error: "x", block: "GONE", hint: "This video was removed or is private" }),
    { ok: false, block: "GONE", hint: "This video was removed or is private" });
  assert.deepEqual(R.probeVerdict(403, { block: "EXPIRED", hint: "h" }), { ok: false, block: "EXPIRED", hint: "h" });
  assert.deepEqual(R.probeVerdict(502, null), { ok: false, block: "UNKNOWN", hint: "" }, "non-JSON 502");
  assert.deepEqual(R.probeVerdict(401, {}), { ok: false, block: "UNKNOWN", hint: "" });
});
test("afterGiveUp: next with the hint (fallback text) while under the cap", () => {
  assert.deepEqual(R.afterGiveUp({ dead: 1, queueLength: 10, hint: "This video was removed or is private" }),
    { do: "next", message: "This video was removed or is private" });
  assert.deepEqual(R.afterGiveUp({ dead: 2, queueLength: 10 }), { do: "next", message: "Couldn't play this one" });
});
test("afterGiveUp: 3 in a row stops with 'Check your connection' (outage)", () => {
  assert.deepEqual(R.afterGiveUp({ dead: 3, content: 0, queueLength: 10, block: "NETWORK" }), { do: "stop", outage: true, message: "Check your connection" });
  assert.deepEqual(R.afterGiveUp({ dead: 3, content: 2, queueLength: 10, block: "EXPIRED" }), { do: "stop", outage: true, message: "Check your connection" });
});
test("afterGiveUp: BOT_WALL stop shows its hint", () => {
  const hint = "YouTube is asking for a bot check — try mobile data or turn off VPN";
  assert.deepEqual(R.afterGiveUp({ dead: 3, queueLength: 10, block: "BOT_WALL", hint }), { do: "stop", outage: true, message: hint });
});
test("afterGiveUp: failures reaching the queue length stop (repeat-all can't spin)", () => {
  assert.equal(R.afterGiveUp({ dead: 1, queueLength: 1, block: "NETWORK" }).do, "stop");
  assert.equal(R.afterGiveUp({ dead: 2, queueLength: 2, block: "NETWORK" }).do, "stop");
  assert.equal(R.afterGiveUp({ dead: 2, queueLength: 3, block: "NETWORK" }).do, "next");
});
test("afterGiveUp: a run of only removed/blocked tracks stops without blaming the connection", () => {
  assert.deepEqual(R.afterGiveUp({ dead: 3, content: 3, queueLength: 10, block: "GONE", hint: "h" }),
    { do: "stop", outage: false, message: "Couldn't play 3 songs in a row" });
  assert.deepEqual(R.afterGiveUp({ dead: 1, content: 1, queueLength: 1, block: "GONE", hint: "Gone hint" }),
    { do: "stop", outage: false, message: "Gone hint" });
});
test("stallLimit: 8 s once metadata is in, longer while the stream resolves", () => {
  assert.equal(R.stallLimit(1), 8000);
  assert.equal(R.stallLimit(4), 8000);
  assert.equal(R.stallLimit(0), 20000);
});
test("a whole track's lifecycle: 3 retries then give up -> next; 3 tracks -> stop", () => {
  let tries = 0, dead = 0, steps = [];
  for (let track = 0; track < 3; track++) {
    tries = 0;
    for (;;) {
      const d = R.onFailure({ block: "NETWORK", tries });
      if (d.do !== "retry") break;
      steps.push(d.delay); tries = d.attempt;
    }
    dead++;
    const g = R.afterGiveUp({ dead, queueLength: 20, block: "NETWORK" });
    steps.push(g.do);
  }
  assert.deepEqual(steps, [1000, 2000, 4000, "next", 1000, 2000, 4000, "next", 1000, 2000, 4000, "stop"]);
});
test("settledTries: only SETTLE_S (15 s) of progress past the resume point clears a track's tries", () => {
  assert.equal(R.SETTLE_S, 15);
  assert.equal(R.settledTries({ tries: 2, from: 100, at: 114.9 }), 2);
  assert.equal(R.settledTries({ tries: 2, from: 100, at: 115 }), 0);
  assert.equal(R.settledTries({ tries: 1, from: 0, at: 15 }), 0);
  assert.equal(R.settledTries({ tries: 3, from: 100, at: 40 }), 3, "a playhead behind the resume point is no progress");
  assert.equal(R.settledTries({ tries: 0, from: 0, at: 999 }), 0);
});
// The machine's loop for one track: fail -> retry (reload resumes at the last position) -> `playing` (no reset)
// -> timeupdates for `plays` seconds (R.settledTries) -> fail again. Returns the decisions in order.
function cycles(plays, max = 12) {
  let tries = 0, pos = 60, from = 0;
  const out = [];
  for (let i = 0; i < max; i++) {
    const d = R.onFailure({ block: null, tries });
    out.push(d.do === "retry" ? d.attempt : d.do);
    if (d.do !== "retry") break;
    tries = d.attempt; from = pos; // reload(): rec.from = rec.pos
    for (let t = 0.25; t <= plays; t += 0.25) tries = R.settledTries({ tries, from, at: from + t });
    pos = from + plays;
  }
  return out;
}
test("brief play after each reload, then a failure again: gives up after 3 reloads (no endless loop)", () => {
  assert.deepEqual(cycles(5), [1, 2, 3, "give-up"]);
  assert.deepEqual(cycles(14.9), [1, 2, 3, "give-up"]);
  assert.deepEqual(cycles(0), [1, 2, 3, "give-up"]);
});
test("sustained play (>= 15 s) after a reload clears the tries: a later failure starts over at 1", () => {
  assert.deepEqual(cycles(20, 6), [1, 1, 1, 1, 1, 1]);
  assert.deepEqual(cycles(15, 4), [1, 1, 1, 1]);
});

// ---- caps, messages, defaults and odd probe answers ----
test("the caps and messages the recovery machine acts on", () => {
  assert.equal(R.MAX_TRIES, 3);
  assert.equal(R.MAX_DEAD, 3);
  assert.equal(R.FALLBACK, "Couldn't play this one");
  assert.equal(R.OFFLINE, "Check your connection");
  assert.deepStrictEqual(R.onFailure({ tries: R.MAX_TRIES - 1 }), { do: "retry", attempt: R.MAX_TRIES, delay: R.backoff(R.MAX_TRIES) });
  assert.deepStrictEqual(R.onFailure({ tries: R.MAX_TRIES }), { do: "give-up" });
  assert.deepStrictEqual(R.onFailure(), { do: "retry", attempt: 1, delay: 1000 });
  assert.deepStrictEqual(R.afterGiveUp({ dead: R.MAX_DEAD - 1, queueLength: 10 }), { do: "next", message: R.FALLBACK });
  assert.deepStrictEqual(R.afterGiveUp({ dead: R.MAX_DEAD, queueLength: 10 }), { do: "stop", outage: true, message: R.OFFLINE });
  assert.deepStrictEqual(R.afterGiveUp(), { do: "stop", outage: true, message: R.OFFLINE }); // one track, failed: stop
  assert.deepStrictEqual(R.afterGiveUp({ dead: 1, content: 1, hint: "Removed" }), { do: "stop", outage: false, message: "Removed" });
  assert.deepStrictEqual(R.afterGiveUp({ dead: 1, content: 1 }), { do: "stop", outage: false, message: R.FALLBACK });
});

test("backoff never goes under 1 s; probeVerdict ignores bodies that aren't strings", () => {
  assert.equal(R.backoff(0), 1000);
  assert.equal(R.backoff(-3), 1000);
  assert.deepStrictEqual(R.probeVerdict(403, { block: 5, hint: 7 }), { ok: false, block: "UNKNOWN", hint: "" });
  assert.deepStrictEqual(R.probeVerdict(500, null), { ok: false, block: "UNKNOWN", hint: "" });
  assert.deepStrictEqual(R.probeVerdict(500, { block: "" }), { ok: false, block: "UNKNOWN", hint: "" });
  assert.deepStrictEqual(R.probeVerdict(undefined), { ok: false, block: "NETWORK", hint: "" });
  assert.deepStrictEqual(R.probeVerdict(404, { block: "BOT_WALL" }), { ok: false, block: "GONE", hint: "" });
  assert.deepStrictEqual(R.probeVerdict(206, { block: "GONE" }), { ok: true, block: null, hint: "" });
  assert.equal(R.isFinal(null), false);
  assert.equal(R.isFinal("BOT_WALL"), false);
});
