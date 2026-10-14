// Fake workers verify JS lifecycle only. They do not replicate Pyodide.
const fs = require("node:fs"),
  vm = require("node:vm"),
  assert = require("node:assert/strict"),
  path = require("node:path");
const sourcePath =
  process.argv[2] ||
  path.resolve(__dirname, "../reclay/web/browser-transport.js");
const receiptPath = process.argv[3] || null;
const label = { textContent: "", dataset: {} },
  cancel = {
    disabled: true,
    addEventListener(_name, fn) {
      this.click = fn;
    },
  };
const events = [],
  workers = [],
  timers = new Map();
let timerSerial = 0;
class Worker {
  constructor(url, options) {
    this.url = String(url);
    this.options = options;
    this.messages = [];
    workers.push(this);
  }
  postMessage(m) {
    this.messages.push(m);
  }
  terminate() {
    this.terminated = true;
  }
}
const document = {
  body: { dataset: { runtime: "browser" } },
  baseURI: "https://example.test/nested/reclay/index.html",
  getElementById(id) {
    return id === "browser-runtime-status" ? label : cancel;
  },
  dispatchEvent(e) {
    events.push(e);
  },
};
const context = {
  document,
  window: {},
  location: { origin: "https://example.test" },
  Worker,
  URL,
  TextEncoder,
  Uint8Array,
  Map,
  Promise,
  Error,
  JSON,
  String,
  Number,
  setTimeout(fn, ms) {
    const id = ++timerSerial;
    timers.set(id, { fn, ms });
    return id;
  },
  clearTimeout(id) {
    timers.delete(id);
  },
  CustomEvent: class {
    constructor(type, options) {
      this.type = type;
      this.detail = options.detail;
    }
  },
  fetch() {
    throw Error("Unexpected network request in lifecycle controls");
  },
  btoa(t) {
    return Buffer.from(t, "binary").toString("base64");
  },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(sourcePath, "utf8"), context);
const rpc = context.window.ReclayTransport;
const tick = async () => {
  await Promise.resolve();
  await Promise.resolve();
};
function emit(w, data) {
  w.onmessage({ data });
}
function ready(w) {
  emit(w, {
    type: "ready",
    version: "314.0.7",
    pythonVersion: "3.14.0",
    sourceVerified: true,
  });
}
async function main() {
  let p = rpc.request("/api/health", {});
  const pRejected = p.catch((e) => e.message);
  const first = workers.at(-1);
  const staleHandler = first.onmessage;
  assert.equal(
    first.url,
    "https://example.test/nested/reclay/browser-worker.mjs",
  );
  assert.equal(first.options.type, "module");
  rpc.cancel();
  assert.match(await pRejected, /cancelled/);
  assert(first.terminated);
  assert(cancel.disabled);
  assert.equal(label.dataset.pyodideVersion, undefined);
  staleHandler({
    data: { type: "ready", version: "old", sourceVerified: true },
  });
  assert.equal(label.dataset.pyodideVersion, undefined);
  p = rpc.request("/api/health", {});
  let second = workers.at(-1);
  assert.notEqual(second, first);
  ready(second);
  await tick();
  assert.equal(second.messages.length, 1);
  emit(second, {
    id: second.messages[0].id,
    ok: true,
    result: { version: "0.3.0" },
  });
  assert.equal((await p).version, "0.3.0");
  assert.equal(label.dataset.pyodideVersion, "314.0.7");
  assert.match(label.textContent, /ready/);
  const inFlight = rpc.request("/api/health", {});
  const inFlightRejected = inFlight.catch((e) => e.message);
  await tick();
  const cancelledWorker = second,
    cancelledHandler = second.onmessage,
    cancelledId = second.messages.at(-1).id;
  rpc.cancel();
  assert.match(await inFlightRejected, /cancelled/);
  assert(cancelledWorker.terminated);
  cancelledHandler({
    data: { id: cancelledId, ok: true, result: { stale: true } },
  });
  assert.match(label.textContent, /cancelled/);
  p = rpc.request("/api/health", {});
  second = workers.at(-1);
  assert.notEqual(second, cancelledWorker);
  ready(second);
  await tick();
  emit(second, {
    id: second.messages.at(-1).id,
    ok: true,
    result: { restart: true },
  });
  assert.equal((await p).restart, true);
  const busy = rpc.request("/api/health", {});
  await tick();
  const busyRejected = rpc.request("/api/health", {}).catch((e) => e.message);
  await tick();
  assert.match(await busyRejected, /already running/);
  emit(second, {
    id: second.messages.at(-1).id,
    ok: true,
    result: { second: true },
  });
  assert.equal((await busy).second, true);
  const timed = rpc.request("/api/health", {});
  const timedRejected = timed.catch((e) => e.message);
  await tick();
  const deadline = [...timers.values()].find((t) => t.ms === 20000);
  assert(deadline);
  const oldCallback = second.onmessage;
  deadline.fn();
  assert.match(await timedRejected, /20-second/);
  assert(second.terminated);
  assert.equal(label.dataset.sourceVerified, undefined);
  oldCallback({
    data: { id: second.messages.at(-1).id, ok: true, result: { stale: true } },
  });
  assert.match(label.textContent, /20-second/);
  const bootFailure = rpc.request("/api/health", {});
  const bootFailureRejected = bootFailure.catch((e) => e.message);
  const third = workers.at(-1);
  emit(third, {
    type: "error",
    id: null,
    error: { message: "source-integrity failure" },
  });
  assert.match(await bootFailureRejected, /source-integrity/);
  assert(third.terminated);
  const cold = rpc.request("/api/health", {});
  const coldRejected = cold.catch((e) => e.message);
  const fourth = workers.at(-1);
  const bootDeadline = [...timers.values()].find((t) => t.ms === 90000);
  assert(bootDeadline);
  bootDeadline.fn();
  assert.match(await coldRejected, /90 seconds/);
  assert(fourth.terminated);
  const count = workers.length;
  await assert.rejects(
    rpc.request("/api/health", { large: "x".repeat(12 * 1048576) }),
    /12 MiB/,
  );
  assert.equal(workers.length, count);
  const receipt = {
    scope:
      "Fake worker/timer JS lifecycle controls only; no Python/WASM replication",
    checks: [
      "relative module worker URL",
      "accepted cancel rejects boot",
      "worker terminated",
      "late ready ignored",
      "fresh restart succeeds",
      "in-flight cancellation rejects and terminates worker",
      "late cancelled result ignored",
      "restart after in-flight cancellation",
      "serialized operation refusal",
      "20-second deadline terminates and revokes runtime labels",
      "late old completion ignored",
      "source boot error reset",
      "90-second boot deadline",
      "request byte bound before worker creation",
    ],
    workersCreated: workers.length,
    resetEvents: events.length,
  };
  if (receiptPath)
    fs.writeFileSync(receiptPath, JSON.stringify(receipt, null, 2) + "\n");
  console.log(JSON.stringify(receipt, null, 2));
}
main().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
