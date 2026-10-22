"use strict";
// Bottom-tab navigation in an iOS Home Screen app (navigator.standalone): the owner's audio paused on a Home -> Library
// tap there, and Web.app may treat a native fragment navigation as a page navigation (WebKit bug 215884 stops capture
// tracks that way). `tabClick` routes the Home / Library tabs with pushState instead. The shipped delegated document
// click listener, `tabClick`, `route`, `parseHash`, `trackNav` and their state run through harness.scope over a small
// history / location model and DOM stubs; the history model fires hashchange (the app's `route`) on native fragment
// navigations and on Back / Forward, and (in the click tests) models the suspected host pause on a native one.
// This proves the routing mechanism only; the pause itself needs a real iPhone Home Screen app.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const { APP, scope } = require("./harness");

// static/app.js with its delegated document click listener compiled where it stands: the
// `  document.addEventListener("click", (e) => {` line and its closing `  });` become `  const docClick = (e) => {` /
// `  };` on the same lines, so harness.scope can run the shipped callback. tabClick joins it only where it's declared.
function appSource(src = fs.readFileSync(APP, "utf8")) {
  const lines = src.split("\n");
  const at = lines.flatMap((l, i) => (l === '  document.addEventListener("click", (e) => {' ? [i] : []));
  assert.equal(at.length, 1, "one delegated document click listener");
  const end = lines.findIndex((l, i) => i > at[0] && /^  \S/.test(l));
  assert.equal(lines[end], "  });", "the listener closes at its indentation");
  lines[at[0]] = "  const docClick = (e) => {";
  lines[end] = "  };";
  const text = lines.join("\n");
  return { text, names: /^ {2}function tabClick\(/m.test(text) ? ["tabClick", "docClick"] : ["docClick"] };
}

// Session history of one document. Every native fragment change is logged ("native" / "replace"), so a test can
// assert that a tab tap never made one; `onNative` runs on each native fragment navigation (the host pause model).
function browser(start) {
  const entries = [{ hash: start, state: null }], log = [];
  let at = 0, onHash = () => {}, onNative = () => {};
  const go = (i) => { at = i; onHash(); }; // a traversal to an entry with another hash fires hashchange
  const location = {
    get hash() { return entries[at].hash; },
    set hash(h) {
      log.push(["native", h]);
      onNative();
      if (h === entries[at].hash) return;
      entries.splice(at + 1, Infinity, { hash: h, state: null });
      go(at + 1);
    },
    replace(u) {
      log.push(["replace", u]);
      const moved = u !== entries[at].hash;
      entries[at] = { hash: u, state: null };
      if (moved) onHash();
    },
  };
  const history = {
    get state() { return entries[at].state; },
    get length() { return entries.length; },
    pushState(s, _t, u) { log.push(["push", u]); entries.splice(at + 1, Infinity, { hash: u, state: structuredClone(s) }); at += 1; },
    replaceState(s, _t, u) { entries[at] = { hash: u ?? entries[at].hash, state: structuredClone(s) }; },
    back() { if (at > 0) go(at - 1); },
    forward() { if (at < entries.length - 1) go(at + 1); },
  };
  return { location, history, log, entries, onHashChange: (fn) => { onHash = fn; }, onNativeNavigation: (fn) => { onNative = fn; } };
}

// Records every read of a property it doesn't know and every write / call: route and tabClick never touch the player.
function untouchedAudio() {
  const touched = [];
  const audio = new Proxy({ paused: true, src: "/api/stream/abc", currentTime: 42 }, {
    set(o, k, v) { touched.push(["set", k]); o[k] = v; return true; },
    get(o, k) {
      if (typeof o[k] === "undefined") touched.push(["get", String(k)]);
      return o[k];
    },
  });
  return { audio, touched };
}

const VIEW_IDS = ["home", "search", "library", "detail"];
function app({ start = "#home", standalone = true, source = appSource() } = {}) {
  const b = browser(start);
  const views = VIEW_IDS.map((v) => ({ id: `view-${v}`, hidden: true, dataset: {} }));
  const tabs = ["home", "library"].map((v) => ({ dataset: { view: v }, classList: { on: false, toggle(_c, on) { this.on = on; } } }));
  const calls = [];
  const note = (name) => (...args) => { calls.push([name, ...args]); };
  const RENDER = Object.fromEntries(VIEW_IDS.map((v) => [v, note(`render:${v}`)]));
  const { audio, touched } = untouchedAudio();
  const s = scope(["VIEWS", "OLD_VIEWS", "DETAIL_RE", "navDepth", "scrollAt", "parseHash", "trackNav", "libFocus", "route", ...source.names], {
    navigator: standalone === null ? {} : { standalone },
    location: b.location,
    history: b.history,
    window: { scrollY: 0, scrollTo: note("scrollTo") },
    document: { body: { dataset: {}, classList: { toggle() {} } } },
    $: (sel) => (sel === "#top" ? { offsetHeight: 50 } : views.find((v) => `#${v.id}` === sel) || null),
    $$: (sel) => (sel === ".view" ? views : sel === ".tab" ? tabs : []),
    state: { view: "home", parent: "home", library: [] },
    detail: {},
    RENDER,
    renderDetail: note("renderDetail"),
    loadPage: note("loadPage"),
    homeDue: () => false,
    loadHome: note("loadHome"),
    loadLibrary: note("loadLibrary"),
    loadPlaylists: note("loadPlaylists"),
    syncTopTitle: () => {},
    npOpen: () => false,
    closeNP: note("closeNP"),
    closeSheet: note("closeSheet"),
    audio,
  }, { src: source.text });
  b.onHashChange(() => s.route());
  s.route(); // the first paint (boot)
  calls.length = 0;
  return { s, b, calls, views, tabs, touched, audio, shown: () => views.find((v) => !v.hidden)?.id };
}

// A click on the bottom tab for `href` (or whatever `target` is), as the delegated document handler receives it.
function click(href, { target, ...over } = {}) {
  const a = { href, target: "", attrs: {}, getAttribute: (n) => (n === "href" ? href : a.attrs[n] ?? null), hasAttribute: (n) => n in a.attrs };
  const e = {
    button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, defaultPrevented: false,
    target: target || { closest: (sel) => (sel === ".tab[href]" ? a : null) },
    preventDefault() { this.defaultPrevented = true; },
    ...over,
  };
  return { e, a };
}

// A tap as the page sees it: the shipped document click listener runs, then the browser follows the link unless the
// listener prevented it. The model: a native fragment navigation in a Home Screen app pauses the playing audio.
function tap(t, href, over) {
  const player = { paused: false };
  t.b.onNativeNavigation(() => { player.paused = true; });
  const { e } = click(href, over);
  t.s.docClick(e);
  if (!e.defaultPrevented) t.b.location.hash = href;
  return { e, player };
}

test("standalone: a Home -> Library tap through the shipped click listener makes no native navigation", () => {
  const t = app();
  const { e, player } = tap(t, "#library");
  assert.equal(e.defaultPrevented, true, "the tab's native fragment navigation is prevented");
  assert.deepEqual(t.b.log, [["push", "#library"]]);
  assert.equal(player.paused, false, "the modelled host pause never happens");
  assert.equal(t.s.state.view, "library");
  assert.equal(t.shown(), "view-library");
  assert.deepEqual(t.b.history.state, { reef: 2, parent: "library" });
  const back = tap(t, "#home");
  assert.equal(back.player.paused, false);
  assert.deepEqual(t.b.log, [["push", "#library"], ["push", "#home"]]);
  assert.equal(t.s.state.view, "home");
});

test("outside a Home Screen app the shipped click listener leaves the tab's native navigation alone", () => {
  const t = app({ standalone: false });
  const { e } = tap(t, "#library");
  assert.equal(e.defaultPrevented, false);
  assert.deepEqual(t.b.log, [["native", "#library"]]);
  assert.equal(t.s.state.view, "library", "hashchange routes it as before");
  assert.deepEqual(t.b.history.state, { reef: 2, parent: "library" });
});

test("standalone: Home -> Library pushes one entry and routes without a native fragment navigation", () => {
  const t = app();
  assert.deepEqual(t.b.entries[0].state, { reef: 1, parent: "home" }); // boot gave the first entry a depth
  const { e } = click("#library");
  assert.equal(t.s.tabClick(e), true);
  assert.equal(e.defaultPrevented, true, "the native fragment navigation is cancelled");
  assert.deepEqual(t.b.log, [["push", "#library"]]);
  assert.equal(t.b.location.hash, "#library");
  assert.equal(t.b.history.length, 2);
  assert.deepEqual(t.b.history.state, { reef: 2, parent: "library" });
  assert.equal(t.s.navDepth, 2);
  assert.equal(t.s.state.view, "library");
  assert.equal(t.shown(), "view-library");
  assert.deepEqual(t.tabs.map((x) => x.classList.on), [false, true]);
  assert.deepEqual(t.calls.filter(([n]) => n !== "scrollTo"), [["loadPlaylists"], ["render:library"]]);
  assert.deepEqual(t.touched, [], "the player is never read or written");
  assert.equal(t.audio.paused, true, "a paused player stays paused (no resume)");
});

test("standalone: Back and Forward over the pushed entry route through hashchange with their depth and parent", () => {
  const t = app();
  t.s.scrollAt.set(1, 0);
  t.s.tabClick(click("#library").e);
  t.s.scrollAt.set(2, 300);
  t.b.history.back();
  assert.equal(t.b.location.hash, "#home");
  assert.equal(t.s.navDepth, 1);
  assert.deepEqual(t.b.history.state, { reef: 1, parent: "home" });
  assert.equal(t.shown(), "view-home");
  t.calls.length = 0;
  t.b.history.forward();
  assert.equal(t.b.location.hash, "#library");
  assert.equal(t.s.navDepth, 2);
  assert.deepEqual(t.b.history.state, { reef: 2, parent: "library" });
  assert.equal(t.shown(), "view-library");
  assert.deepEqual(t.calls.find(([n]) => n === "scrollTo"), ["scrollTo", 0, 300], "Forward restores the Library's scroll");
  assert.deepEqual(t.b.log, [["push", "#library"]], "only the tap made an entry");
  assert.deepEqual(t.touched, []);
});

test("standalone: Library -> Home also uses pushState, and a detail page opened from Home keeps its parent on Back", () => {
  const t = app({ start: "#album/a1" });
  assert.equal(t.s.state.parent, "home");
  const { e } = click("#library");
  t.s.tabClick(e);
  assert.equal(t.s.state.view, "library");
  assert.deepEqual(t.b.history.state, { reef: 2, parent: "library" });
  t.s.tabClick(click("#home").e);
  assert.deepEqual(t.b.log, [["push", "#library"], ["push", "#home"]]);
  assert.deepEqual(t.b.history.state, { reef: 3, parent: "home" });
  t.b.history.back();
  t.b.history.back();
  assert.equal(t.b.location.hash, "#album/a1");
  assert.equal(t.s.state.view, "detail");
  assert.equal(t.s.state.parent, "home");
  assert.equal(t.s.navDepth, 1);
});

test("standalone: tapping the tab already shown adds no entry and doesn't re-route", () => {
  const t = app({ start: "#library" });
  const { e } = click("#library");
  assert.equal(t.s.tabClick(e), true);
  assert.equal(e.defaultPrevented, true);
  assert.deepEqual(t.b.log, []);
  assert.equal(t.b.history.length, 1);
  assert.deepEqual(t.calls, []);
});

test("outside a Home Screen app the tab click stays native", () => {
  for (const standalone of [false, null]) {
    const t = app({ standalone });
    const { e } = click("#library");
    assert.equal(t.s.tabClick(e), false);
    assert.equal(e.defaultPrevented, false);
    assert.deepEqual(t.b.log, []);
    assert.deepEqual(t.calls, []);
  }
});

test("modified, non-primary, targeted, download and already-handled clicks stay native", () => {
  const cases = [{ metaKey: true }, { ctrlKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 }, { defaultPrevented: true }];
  for (const over of cases) {
    const t = app();
    const { e } = click("#library", over);
    const prevented = e.defaultPrevented;
    assert.equal(t.s.tabClick(e), false, JSON.stringify(over));
    assert.equal(e.defaultPrevented, prevented, JSON.stringify(over));
    assert.deepEqual(t.b.log, []);
  }
  for (const set of [(a) => { a.target = "_blank"; }, (a) => { a.attrs.download = ""; }]) {
    const t = app();
    const { e, a } = click("#library");
    set(a);
    assert.equal(t.s.tabClick(e), false);
    assert.equal(e.defaultPrevented, false);
    assert.deepEqual(t.b.log, []);
  }
});

test("other links (Search, See all, browse pages, another hash on a tab) stay native", () => {
  const t = app();
  const plain = { closest: () => null }; // the Search button, a shelf's See all, a page link: not a .tab
  for (const e of [click("#search", { target: plain }).e, click("#library", { target: plain }).e, click("#search").e, click("#album/x").e]) {
    assert.equal(t.s.tabClick(e), false);
    assert.equal(e.defaultPrevented, false);
  }
  const svg = click("#library", { target: {} }).e; // a target without closest (e.g. an old SVG <use> instance)
  assert.equal(t.s.tabClick(svg), false);
  assert.deepEqual(t.b.log, []);
  assert.deepEqual(t.calls, []);
});

test("programmatic routes and legacy hashes keep their native navigation", () => {
  const t = app();
  t.b.location.hash = "#kept"; // an old link: route redirects with location.replace
  assert.equal(t.b.location.hash, "#library");
  assert.equal(t.s.state.view, "library");
  assert.deepEqual(t.b.log, [["native", "#kept"], ["replace", "#library"]]);
  assert.equal(t.b.history.length, 2);
});
