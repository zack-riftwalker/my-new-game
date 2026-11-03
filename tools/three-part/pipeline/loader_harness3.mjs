// Range-transport harness (M5 zero-touch publishing): the real loader v2
// runs against the publish2 tree; segments arrive ONLY as HTTP Range
// requests against the 42 original part files.
//  A: stage 1 cold -> sha equals M1 stage 1; range requests counted
//  B: stage 1 warm -> only manifest+spec over the network
//  C: stage 2 -> sha equals M1 stage 2
//  D: stage 3 -> sha equals the ORIGINAL hk.data
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import vm from "node:vm";

const html = readFileSync("/home/hatch/workspace/hk-parts/loader/index.html", "utf8");
const script = html.match(/<script>\n([\s\S]*?)\n    <\/script>/)[1];
const BASE = "http://127.0.0.1:8902/";

const EXPECT = {
  A: { stage: "1", size: 499157940, sha: "026dffc6f88beef08784fd1c7fd5db17c87510f6e9b4e54a0ea7c978ddea1443" },
  B: { stage: "1", size: 499157940, sha: "026dffc6f88beef08784fd1c7fd5db17c87510f6e9b4e54a0ea7c978ddea1443", maxNetwork: 2 },
  C: { stage: "2", size: 691875491, sha: process.argv[2] },
  D: { stage: "3", size: 869012889, sha: "26620a80f44b8411672ee48f381c7af349aa79b9aa2a6c6e9e2d993b1b238d1e" },
};

const cacheStore = new Map();
const localStorageData = new Map();
const realFetch = globalThis.fetch;
let networkFetches = 0, rangeRequests = 0;

function makeContext(captured) {
  const elements = new Map();
  const makeElement = (id) => ({
    id, hidden: false, style: {}, textContent: "", className: "", children: [], dataset: {},
    addEventListener() {}, removeEventListener() {}, remove() {},
    appendChild(c) { this.children.push(c); if (c.tag === "script") captured.scriptSrc = c.src; },
    querySelector() { return makeElement("q"); },
  });
  const getEl = (id) => { if (!elements.has(id)) elements.set(id, makeElement(id)); return elements.get(id); };
  const context = {
    console, setTimeout, clearTimeout, setInterval, clearInterval, performance,
    URLSearchParams,
    atob: (s) => Buffer.from(s, "base64").toString("binary"),
    TextDecoder, Uint8Array, ArrayBuffer, Map, Set, Promise, JSON, Math, Date, RegExp, Error, Object, Array, String, Number, Boolean, parseInt, parseFloat, isNaN, encodeURIComponent, decodeURIComponent,
    URL: class extends URL { static createObjectURL(blob) { captured.blobs.push(blob); return "blob:captured:" + captured.blobs.length; } static revokeObjectURL() {} },
    Blob,
    Response: class { constructor(body) { this._body = body; } async blob() { return this._body; } },
    location: { href: BASE, search: "?manifest=" + BASE + "manifest.local.json", reload() { captured.reloaded = true; } },
    navigator: { userAgent: "NodeHarness" },
    localStorage: {
      getItem: (k) => (localStorageData.has(k) ? localStorageData.get(k) : null),
      setItem: (k, v) => localStorageData.set(k, String(v)),
      removeItem: (k) => localStorageData.delete(k),
    },
    document: {
      baseURI: "https://cdn.jsdelivr.net/gh/Craftingdead/hollow-knight@latest/",
      getElementById: getEl,
      querySelector: (sel) => getEl(sel.replace("#", "")),
      createElement: (tag) => { const el = makeElement(tag); el.tag = tag; return el; },
      addEventListener() {},
      body: makeElement("body"),
      visibilityState: "visible",
    },
    fetch: async (url, opts) => {
      const u = String(url);
      if (u.startsWith("https://cdn.jsdelivr.net/")) {
        if (opts && opts.method === "HEAD") return { ok: true, headers: { get: () => "1048576" } };
        networkFetches++;
        return realFetch(BASE + "parts/v1/patch/bundle_header_stage1.bin.b64").then(async r => {
          // wasm mock: return a small blob response shaped like fetch result
          const buf = await r.arrayBuffer();
          return { ok: true, status: 200, body: new Blob([buf]).stream(), arrayBuffer: async () => buf };
        });
      }
      networkFetches++;
      if (opts && opts.headers && opts.headers.Range) rangeRequests++;
      return realFetch(u, opts);
    },
    caches: {
      open: async () => ({
        match: async (url) => { const b = cacheStore.get(String(url)); return b ? { blob: async () => b } : undefined; },
        put: async (url, res) => { cacheStore.set(String(url), await res.blob()); },
      }),
    },
    indexedDB: undefined,
    addEventListener() {}, removeEventListener() {},
    innerWidth: 1280, innerHeight: 800, devicePixelRatio: 1,
    requestAnimationFrame: (cb) => setTimeout(() => cb(performance.now()), 16),
    cancelAnimationFrame: (id) => clearTimeout(id),
    alert: (m) => { captured.alert = m; },
    createUnityInstance: () => Promise.resolve({}),
    screen: { orientation: { lock: async () => {} } },
    window: null,
  };
  context.window = context;
  context.self = context;
  return vm.createContext(context);
}

async function run(label) {
  const exp = EXPECT[label];
  localStorageData.set("hk.installedStage", exp.stage);
  networkFetches = 0; rangeRequests = 0;
  const captured = { blobs: [], scriptSrc: null };
  const ctx = makeContext(captured);
  vm.runInContext(script, ctx);
  const t0 = Date.now();
  while (!captured.scriptSrc) {
    if (Date.now() - t0 > 180000) throw new Error(label + ": timeout; alert=" + captured.alert);
    await new Promise(r => setTimeout(r, 200));
  }
  const blob = captured.blobs.find(b => b.size === exp.size);
  if (!blob) throw new Error(label + ": no blob of expected size; got " + captured.blobs.map(b => b.size).join(","));
  const buf = Buffer.from(await blob.arrayBuffer());
  const sha = createHash("sha256").update(buf).digest("hex");
  if (sha !== exp.sha) throw new Error(label + ": SHA MISMATCH " + sha);
  if (exp.maxNetwork !== undefined && networkFetches > exp.maxNetwork)
    throw new Error(label + `: expected <= ${exp.maxNetwork} network fetches on warm cache, got ${networkFetches}`);
  console.log(`RUN ${label} OK: stage ${exp.stage} assembled ${exp.size} bytes, sha verified, fetches = ${networkFetches} (range: ${rangeRequests}), cache entries = ${cacheStore.size}`);
}

await run("A");
await run("B");
await run("C");
await run("D");
console.log("ALL RANGE-TRANSPORT RUNS PASSED");
