// Headless boot test for the three-part loader via CDP (Node >= 22, global WebSocket).
// Usage: node boot_test.mjs [url] [maxSeconds]
import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync } from "node:fs";

const URL_TO_OPEN = process.argv[2] || "http://127.0.0.1:8901/";
const MAX_SECONDS = parseInt(process.argv[3] || "720", 10);
const OUT = "/home/hatch/workspace/hk-parts/logs/boottest";
mkdirSync(OUT, { recursive: true });

const chrome = spawn("/opt/meta-chromium/chrome", [
  "--headless=new",
  "--remote-debugging-port=9223",
  "--user-data-dir=/tmp/hk-test-profile",
  "--use-angle=swiftshader",
  "--enable-unsafe-swiftshader",
  "--disable-dev-shm-usage",
  "--no-sandbox",
  "--window-size=1280,800",
  "--mute-audio",
  "about:blank",
], { stdio: ["ignore", "pipe", "pipe"] });
chrome.stderr.on("data", () => {});

const sleep = (ms) => new Promise(r => setTimeout(r, ms));
async function getWsUrl() {
  for (let i = 0; i < 50; i++) {
    try {
      const res = await fetch("http://127.0.0.1:9223/json/version");
      const j = await res.json();
      if (j.webSocketDebuggerUrl) return j.webSocketDebuggerUrl;
    } catch {}
    await sleep(300);
  }
  throw new Error("chrome debugging endpoint did not come up");
}

const wsUrl = await getWsUrl();
const ws = new WebSocket(wsUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

let nextId = 1;
const pending = new Map();
const events = [];
ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  else if (msg.method) events.push(msg);
};
function send(method, params = {}, sessionId) {
  const id = nextId++;
  return new Promise((resolve) => {
    pending.set(id, resolve);
    ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
}

const { result: { targetId } } = await send("Target.createTarget", { url: "about:blank" });
const { result: { sessionId } } = await send("Target.attachToTarget", { targetId, flatten: true });
const S = (m, p) => send(m, p, sessionId);

await S("Runtime.enable");
await S("Page.enable");
await S("Log.enable");
await S("Page.navigate", { url: URL_TO_OPEN });
console.log("navigated to", URL_TO_OPEN);

async function evaluate(expression) {
  const r = await S("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (r.result?.exceptionDetails) return { error: r.result.exceptionDetails.text };
  return r.result?.result?.value;
}
async function screenshot(name) {
  const r = await S("Page.captureScreenshot", { format: "png" });
  if (r.result?.data) { writeFileSync(`${OUT}/${name}.png`, Buffer.from(r.result.data, "base64")); console.log("screenshot:", name); }
}

const stateExpr = `(() => {
  const lt = document.getElementById('loading-title');
  const ls = document.getElementById('loading-sub');
  const bar = document.getElementById('dl-bar-full');
  const canvas = document.getElementById('unity-canvas');
  const gate = document.getElementById('gate-banner');
  const partsBtn = document.getElementById('parts-button');
  return {
    loadingBoxGone: !document.getElementById('loading-box'),
    title: lt ? lt.textContent : null,
    sub: ls ? ls.textContent : null,
    barPct: bar ? bar.style.width : null,
    canvas: canvas ? { w: canvas.width, h: canvas.height, display: canvas.style.display } : null,
    gateVisible: gate ? !gate.hidden : null,
    partsBtnVisible: partsBtn ? !partsBtn.hidden : null,
    lsStage: localStorage.getItem('hk.installedStage'),
  };
})()`;

let booted = false;
let lastState = "";
const t0 = Date.now();
let shotAtBoot = false;
while ((Date.now() - t0) / 1000 < MAX_SECONDS) {
  await sleep(8000);
  const st = await evaluate(stateExpr).catch(() => null);
  const line = JSON.stringify(st);
  if (line !== lastState) { console.log(`[t+${Math.round((Date.now() - t0) / 1000)}s]`, line); lastState = line; }
  // surface console errors
  for (const e of events.splice(0)) {
    if (e.method === "Runtime.exceptionThrown") console.log("EXCEPTION:", JSON.stringify(e.params.exceptionDetails?.exception?.description || e.params.exceptionDetails?.text).slice(0, 500));
    if (e.method === "Log.entryAdded" && e.params.entry.level === "error") console.log("LOG-ERROR:", e.params.entry.text.slice(0, 300));
    if (e.method === "Runtime.consoleAPICalled" && e.params.type === "error") console.log("CONSOLE-ERROR:", e.params.args.map(a => a.value || a.description || "").join(" ").slice(0, 300));
  }
  if (st && st.loadingBoxGone && !booted) {
    booted = true;
    console.log("BOOT SUCCESS: loading box removed, Unity instance resolved");
    await sleep(15000);
    await screenshot("after_boot");
    shotAtBoot = true;
    const dbs = await evaluate(`(async () => { try { const d = await indexedDB.databases(); return d.map(x => x.name); } catch (e) { return ['ERR:' + e.message]; } })()`);
    console.log("IndexedDB databases:", JSON.stringify(dbs));
    // give the menu time to render, more screenshots
    await sleep(20000);
    await screenshot("menu_wait");
    break;
  }
}
if (!booted) {
  console.log("BOOT DID NOT COMPLETE within", MAX_SECONDS, "s");
  await screenshot("timeout_state");
}
try { await evaluate(`document.title`); } catch {}
ws.close();
chrome.kill("SIGKILL");
process.exit(booted ? 0 : 1);
