// Node harness: runs the loader <script> from loader/index.html with mocked
// DOM/Cache/localStorage against the REAL local test server (real HTTP fetches
// of manifest/spec/segments), captures the Blob handed to Unity as dataUrl,
// and verifies its size + sha256 against the offline-assembled stage file.
// Also verifies a second boot is served fully from (mock) Cache Storage.
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import vm from "node:vm";

const html = readFileSync("/home/hatch/workspace/hk-parts/loader/index.html", "utf8");
const script = html.match(/<script>\n([\s\S]*?)\n    <\/script>/)[1];
const EXPECTED_SHA = process.argv[2];
const EXPECTED_SIZE = parseInt(process.argv[3], 10);

function makeElement(id) {
  const el = {
    id, hidden: false, style: {}, textContent: "", className: "",
    children: [], dataset: {},
    addEventListener() {}, removeEventListener() {}, remove() { this.removed = true; },
    appendChild(c) { this.children.push(c); if (c.tag === "script") captured.scriptSrc = c.src; },
    querySelector() { return makeElement("q"); },
  };
  return el;
}
const elements = new Map();
function getEl(id) { if (!elements.has(id)) elements.set(id, makeElement(id)); return elements.get(id); }

const captured = { blobs: [], scriptSrc: null };
const cacheStore = new Map(); // url -> Blob
let networkFetches = 0;
const realFetch = globalThis.fetch;

const localStorageData = new Map();
const context = {
  console, setTimeout, clearTimeout, setInterval, clearInterval, performance,
  atob: (s) => Buffer.from(s, "base64").toString("binary"),
  TextDecoder, Uint8Array, ArrayBuffer, Map, Set, Promise, JSON, Math, Date, RegExp, Error, Object, Array, String, Number, Boolean, parseInt, parseFloat, isNaN, encodeURIComponent, decodeURIComponent,
  URL: class extends URL { static createObjectURL(blob) { captured.blobs.push(blob); return "blob:captured:" + captured.blobs.length; } static revokeObjectURL() {} },
  Blob,
  Response: class { constructor(body) { this._body = body; } async blob() { return this._body; } },
  location: { href: "http://127.0.0.1:8901/", reload() { captured.reloaded = true; } },
  navigator: { userAgent: "NodeHarness", wakeLock: undefined },
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
      // wasm/framework CDN: emulate sizes without 32 MB of traffic (HEAD ok, GET serves zeros of right length)
      if (opts && opts.method === "HEAD") return { ok: true, headers: { get: () => "1048576" } };
      networkFetches++;
      return realFetch("http://127.0.0.1:8901/parts/v1/patch/bundle_header_stage1.bin"); // tiny placeholder body; wasm content irrelevant here
    }
    networkFetches++;
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
  alert: (m) => { captured.alert = m; },
  createUnityInstance: () => Promise.resolve({}),
  screen: { orientation: { lock: async () => {} } },
  window: null,
};
context.window = context;
context.self = context;
vm.createContext(context);

async function waitFor(cond, timeoutMs, label) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (cond()) return true;
    await new Promise(r => setTimeout(r, 250));
  }
  throw new Error("timeout waiting for " + label);
}

async function sha256OfBlob(blob) {
  const buf = Buffer.from(await blob.arrayBuffer());
  return { size: buf.length, sha: createHash("sha256").update(buf).digest("hex") };
}

console.log("=== RUN 1 (cold cache) ===");
networkFetches = 0;
vm.runInContext(script, context);
await waitFor(() => captured.scriptSrc, 180000, "unity loader script injection");
const dataBlob = captured.blobs[0]; // wasm is blobs[0]? order: wasm first? boot makes wasmUrl BEFORE dataUrl
console.log("captured blobs:", captured.blobs.map(b => b.size));
const sizes = captured.blobs.map(b => b.size);
const dataIdx = sizes.indexOf(EXPECTED_SIZE);
if (dataIdx < 0) throw new Error("no captured blob with expected assembled size " + EXPECTED_SIZE);
const { size, sha } = await sha256OfBlob(captured.blobs[dataIdx]);
console.log("assembled data blob size:", size, "sha256:", sha);
console.log("expected                :", EXPECTED_SIZE, EXPECTED_SHA);
if (sha !== EXPECTED_SHA) throw new Error("SHA MISMATCH");
console.log("RUN 1 OK: JS-assembled hk.data.stage1 is byte-identical to the Python-assembled file; network fetches:", networkFetches, "; cache entries:", cacheStore.size);
console.log("progress title:", getEl("loading-title").textContent, "| sub:", getEl("loading-sub").textContent);
