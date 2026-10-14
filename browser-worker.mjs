// Module worker: trusted source and a pinned Pyodide runtime, data-only RPCs.
const PYODIDE_VERSION = "314.0.7";
const RUNTIME_BASE = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/";
const MAX_JSON = 12 * 1024 * 1024;
const MAX_SOURCE = 1024 * 1024;
const SOURCES = ["__init__.py", "engine.py", "standalone.py", "relocation.py", "browser_dispatch.py"];
const ROUTES = new Set([
  "/api/health", "/health", "/api/infer", "/api/parse", "/api/edit", "/api/export",
  "/api/relocation/infer", "/api/relocation/inspect", "/api/relocation/resize", "/api/relocation/export",
]);
const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true });
let runtimePromise = null;
let busy = false;

function fail(code, message) {
  const error = new Error(message);
  error.code = code;
  throw error;
}

function progress(message) {
  self.postMessage({ type: "progress", message });
}

async function readLimited(relative, maximum) {
  const url = new URL(relative, import.meta.url);
  if (url.origin !== self.location.origin) fail("source-origin", "Trusted sources must use this page's origin");
  const response = await fetch(url, { credentials: "omit", cache: "no-store", redirect: "error" });
  if (!response.ok) fail("source-fetch", `Cannot load trusted asset ${relative}: HTTP ${response.status}`);
  const declared = Number(response.headers.get("content-length"));
  if (Number.isFinite(declared) && declared > maximum) fail("source-bound", "Trusted asset exceeds its byte bound");
  const reader = response.body?.getReader();
  if (!reader) fail("source-fetch", "Streaming source reads are unavailable");
  let size = 0;
  const chunks = [];
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > maximum) {
        await reader.cancel();
        fail("source-bound", "Trusted asset exceeds its byte bound");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let at = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, at);
    at += chunk.byteLength;
  }
  return bytes;
}

async function trustedSources() {
  progress("Checking the shipped Python source bytes…");
  if (!self.crypto?.subtle) fail("secure-origin", "The browser lab requires HTTPS or localhost for source integrity checks");
  const manifest = JSON.parse(decoder.decode(await readLimited("./browser-manifest.json", MAX_SOURCE)));
  if (!manifest || !Array.isArray(manifest.files) || manifest.files.length > 512) {
    fail("source-manifest", "Trusted source manifest is missing or malformed");
  }
  const rows = new Map();
  for (const name of SOURCES) {
    const path = `python/${name}`;
    const matches = manifest.files.filter((row) => row && row.path === path);
    if (matches.length !== 1) fail("source-manifest", `Expected exactly one manifest entry for ${path}`);
    const row = matches[0];
    if (!Number.isSafeInteger(row.bytes) || row.bytes < 1 || row.bytes > MAX_SOURCE || !/^[0-9a-f]{64}$/.test(row.sha256)) {
      fail("source-manifest", `Invalid source identity for ${path}`);
    }
    rows.set(name, row);
  }
  // All files are fetched and checked before mounting or executing any source.
  return await Promise.all(SOURCES.map(async (name) => {
    const bytes = await readLimited(`./python/${name}`, MAX_SOURCE);
    const digest = new Uint8Array(await self.crypto.subtle.digest("SHA-256", bytes));
    const sha256 = Array.from(digest, (byte) => byte.toString(16).padStart(2, "0")).join("");
    const row = rows.get(name);
    if (bytes.byteLength !== row.bytes || sha256 !== row.sha256) fail("source-integrity", `Trusted source bytes changed: ${name}`);
    decoder.decode(bytes); // Reject non-UTF-8 source before the runtime sees it.
    return { name, bytes };
  }));
}

async function initialize() {
  const source = await trustedSources();
  progress("Loading Python in this browser. The first visit downloads the pinned runtime…");
  const { loadPyodide } = await import(`${RUNTIME_BASE}pyodide.mjs`);
  const pyodide = await loadPyodide({ indexURL: RUNTIME_BASE });
  if (pyodide.version !== PYODIDE_VERSION) fail("runtime-version", "The loaded Python runtime does not match its pinned version");
  progress("Starting the verified local inference engine…");
  const directory = "/reclay_browser/reclay";
  pyodide.FS.mkdirTree(directory);
  for (const { name, bytes } of source) pyodide.FS.writeFile(`${directory}/${name}`, bytes);
  // Fixed trusted code only. Request strings never become Python source.
  pyodide.runPython("import sys\nsys.path.insert(0, '/reclay_browser')\nfrom reclay.browser_dispatch import dispatch_json as _reclay_dispatch_json");
  const dispatch = pyodide.globals.get("_reclay_dispatch_json");
  const pythonVersion = pyodide.runPython("sys.version.split()[0]");
  self.postMessage({ type: "ready", runtime: "Pyodide", version: PYODIDE_VERSION, pythonVersion, sourceVerified: true });
  return { pyodide, dispatch };
}

function validateMessage(message) {
  if (!message || typeof message !== "object" || Array.isArray(message)) fail("invalid-message", "Worker request must be an object");
  const keys = Object.keys(message);
  if (keys.length !== 3 || !keys.includes("id") || !keys.includes("path") || !keys.includes("body")) fail("invalid-message", "Worker accepts only id, path and body");
  const id = message.id;
  if (!(Number.isSafeInteger(id) && id >= 0) && !(typeof id === "string" && id.length >= 1 && id.length <= 64)) fail("invalid-id", "Worker request identity is invalid");
  if (typeof message.path !== "string" || !ROUTES.has(message.path)) fail("unknown-operation", "Unknown browser operation");
  if (!message.body || typeof message.body !== "object" || Array.isArray(message.body)) fail("invalid-input", "Request body must be a JSON object");
  const serialized = JSON.stringify(message.body);
  if (encoder.encode(serialized).byteLength > MAX_JSON) fail("json-bound", "Request JSON exceeds 12 MiB");
  return serialized;
}

self.onmessage = async ({ data: message }) => {
  let id = null;
  try {
    const serialized = validateMessage(message);
    id = message.id;
    if (busy) fail("busy", "One browser operation is already running");
    busy = true;
    try {
      runtimePromise ??= initialize();
      const runtime = await runtimePromise;
      const encoded = runtime.dispatch(message.path, serialized);
      if (typeof encoded !== "string" || encoder.encode(encoded).byteLength > MAX_JSON) fail("output-bound", "Browser result exceeds 12 MiB");
      const answer = JSON.parse(encoded);
      if (!answer || typeof answer.ok !== "boolean") fail("invalid-result", "Python returned an invalid response");
      self.postMessage({ id, ...answer });
    } finally {
      busy = false;
    }
  } catch (error) {
    if (id === null && message && (typeof message.id === "string" || Number.isSafeInteger(message.id))) id = message.id;
    self.postMessage({ id, ok: false, error: { code: error.code || "browser-runtime", message: String(error.message || error).slice(0, 2048), context: {} } });
  }
};

// The transport creates this worker only on the first API call, then waits for
// ready before sending that operation. Boot is eager inside the lazy worker.
runtimePromise = initialize();
runtimePromise.catch((error) => {
  self.postMessage({ type: "error", id: null, ok: false, error: { code: error.code || "browser-runtime", message: String(error.message || error).slice(0, 2048), context: {} } });
});
