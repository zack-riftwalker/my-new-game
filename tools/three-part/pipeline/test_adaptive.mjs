// Unit test for the M7 adaptive-resolution controller + quality modes.
// Loads the real loader script with DOM stubs and drives
// window.__hkAdaptive.sample() with synthetic fps/time values.
import fs from "node:fs";
import vm from "node:vm";

const html = fs.readFileSync(new URL("../loader/index.html", import.meta.url), "utf8");
const script = html.match(/<script>\n([\s\S]*?)\n    <\/script>/)[1];

function makeEnv(quality) {
    const store = {};
    if (quality) store["hk.quality"] = quality;
    const el = () => ({
        style: {}, dataset: {}, hidden: false, textContent: "", children: [],
        classList: { add() {}, remove() {}, contains: () => false },
        addEventListener() {}, querySelectorAll: () => [],
        appendChild() {}, remove() {},
    });
    const els = new Map();
    const documentMock = {
        getElementById: (id) => { if (!els.has(id)) els.set(id, el()); return els.get(id); },
        querySelectorAll: () => [], createElement: () => el(),
        addEventListener() {}, body: el(), hidden: false, visibilityState: "visible",
    };
    const windowMock = {
        location: { href: "https://example.test/game/index.html", search: "", origin: "https://example.test" },
        addEventListener() {}, matchMedia: () => ({ matches: false }),
        requestAnimationFrame: () => 0, cancelAnimationFrame: () => {},
        setInterval: () => 0, setTimeout: () => 0,
        localStorage: {
            getItem: (k) => (k in store ? store[k] : null),
            setItem: (k, v) => { store[k] = String(v); },
            removeItem: (k) => { delete store[k]; },
        },
    };
    const performanceMock = { now: () => 0 };
    const ctx = vm.createContext({
        window: windowMock, document: documentMock, localStorage: windowMock.localStorage,
        navigator: { userAgent: "node-test" }, performance: performanceMock,
        URLSearchParams, URL, console, setTimeout, clearTimeout, setInterval, clearInterval,
        fetch: async () => { throw new Error("no network in unit test"); },
        caches: undefined, indexedDB: undefined,
        KeyboardEvent: class { constructor(t, o) { this.type = t; Object.assign(this, o); } },
        TextDecoder, Blob, Promise, Map, Set, Uint8Array, ArrayBuffer, Math, JSON, Date, Error, RegExp, String, Number, parseInt, parseFloat, isNaN, encodeURIComponent, decodeURIComponent, btoa: (s) => Buffer.from(s, "binary").toString("base64"), atob: (s) => Buffer.from(s, "base64").toString("binary"),
    });
    ctx.window.document = documentMock;
    ctx.window.performance = performanceMock;
    ctx.window.navigator = ctx.navigator;
    return { ctx, store };
}

let failures = 0;
function check(name, cond) { console.log((cond ? "PASS" : "FAIL") + "  " + name); if (!cond) failures++; }

// --- auto mode: sustained low fps steps down, cooldown, floor ---
{
    const { ctx } = makeEnv(null);
    vm.runInContext(script, ctx, { filename: "loader.js" });
    const A = ctx.window.__hkAdaptive;
    check("auto starts at level 0 (480p)", A.level() === 0);
    let t = 0;
    check("one low sample: no change yet", A.sample(20, t) === false && A.level() === 0);
    check("second low sample: step down to 432p", A.sample(20, t += 2500) === true && A.level() === 1);
    check("cooldown blocks instant second drop", A.sample(15, t += 2500) === false && A.level() === 1);
    A.sample(15, t += 2500); // votes accumulate during cooldown
    check("after cooldown + votes: step down to 360p", A.sample(15, t += 6000) === true && A.level() === 2);
    for (let i = 0; i < 6; i++) A.sample(10, t += 15000);
    check("never below 360p", A.level() === 2);
    // recovery needs sustained high fps
    let changed = false;
    for (let i = 0; i < 4; i++) changed = A.sample(60, t += 2500) || changed;
    check("4 high samples: still no step up", !changed && A.level() === 2);
    check("5th high sample: step up to 432p", A.sample(60, t += 2500) === true && A.level() === 1);
}

// --- fixed mode: samples ignored, level pinned ---
{
    const { ctx } = makeEnv("360");
    vm.runInContext(script, ctx, { filename: "loader.js" });
    const A = ctx.window.__hkAdaptive;
    check("fixed 360 starts at level 2", A.level() === 2);
    check("fixed mode ignores samples", A.sample(60, 999999) === false && A.level() === 2);
}

// --- mid-range fps resets votes (no flapping) ---
{
    const { ctx } = makeEnv(null);
    vm.runInContext(script, ctx, { filename: "loader.js" });
    const A = ctx.window.__hkAdaptive;
    let t = 0, changed = false;
    for (const fps of [20, 45, 20, 45, 20, 45]) changed = A.sample(fps, t += 2500) || changed;
    check("alternating fps never changes level", !changed && A.level() === 0);
}

console.log(failures ? `\n${failures} FAILURE(S)` : "\nALL ADAPTIVE TESTS PASSED");
process.exit(failures ? 1 : 0);
