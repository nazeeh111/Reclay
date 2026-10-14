/* Browser-only transport for the unchanged Python inference engine. */
(() => {
  "use strict";
  if (document.body.dataset.runtime !== "browser") return;
  const MAX_BODY = 12 * 1048576;
  const BASE = new URL("./", document.baseURI);
  const pending = new Map();
  let worker = null,
    boot = null,
    nextId = 0,
    ready = false,
    generation = 0;
  const label = document.getElementById("browser-runtime-status");
  const cancelButton = document.getElementById("browser-cancel");
  const text = (message) => {
    if (label) label.textContent = message;
  };
  function reset(reason) {
    generation++;
    if (worker) {
      worker.onmessage = null;
      worker.onerror = null;
      worker.terminate();
    }
    worker = null;
    ready = false;
    if (label) {
      delete label.dataset.pyodideVersion;
      delete label.dataset.pythonVersion;
      delete label.dataset.sourceVerified;
    }
    if (boot) {
      clearTimeout(boot.timer);
      boot.reject(Error(reason));
      boot = null;
    }
    for (const entry of pending.values()) {
      clearTimeout(entry.timer);
      entry.reject(Error(reason));
    }
    pending.clear();
    if (cancelButton) cancelButton.disabled = true;
    text(reason + " Start another operation to load a fresh worker.");
    document.dispatchEvent(
      new CustomEvent("reclay-runtime-reset", { detail: reason }),
    );
  }
  function ensureWorker() {
    if (ready) return Promise.resolve();
    if (boot) return boot.promise;
    let resolve, reject;
    const promise = new Promise((yes, no) => {
      resolve = yes;
      reject = no;
    });
    boot = {
      promise,
      resolve,
      reject,
      timer: setTimeout(
        () => reset("Browser runtime loading exceeded 90 seconds."),
        90000,
      ),
    };
    text("Loading the browser Python runtime…");
    if (cancelButton) cancelButton.disabled = false;
    try {
      const epoch = ++generation;
      worker = new Worker(new URL("browser-worker.mjs", BASE), {
        type: "module",
      });
      worker.onmessage = ({ data }) => {
        if (epoch !== generation) return;
        if (data.type === "progress") {
          text(data.message);
          return;
        }
        if (data.type === "ready") {
          if (label) {
            label.dataset.pyodideVersion = data.version;
            label.dataset.pythonVersion = data.pythonVersion;
            label.dataset.sourceVerified = String(data.sourceVerified);
          }
          ready = true;
          clearTimeout(boot.timer);
          boot.resolve();
          boot = null;
          text("Python engine ready. Files stay in this browser.");
          return;
        }
        if (
          data.type === "boot-error" ||
          (data.type === "error" && data.id == null)
        ) {
          reset(
            "Browser runtime failed: " + (data.error?.message || data.error),
          );
          return;
        }
        const entry = pending.get(data.id);
        if (!entry) return;
        clearTimeout(entry.timer);
        pending.delete(data.id);
        text(
          data.ok
            ? "Python engine ready. Files stay in this browser."
            : "Operation refused. Python engine ready.",
        );
        if (data.ok) {
          if (entry.exportModelId && typeof data.result?.source === "string") {
            data.result.source +=
              "\n# Created with Reclay: https://github.com/nazeeh111/Reclay\n# Selected model SHA256: " +
              entry.exportModelId +
              "\n";
          }
          entry.resolve(data.result);
        } else
          entry.reject(
            Error(data.error?.message || data.error || "Operation failed."),
          );
        if (cancelButton) cancelButton.disabled = !pending.size;
      };
      worker.onerror = (event) => {
        event.preventDefault();
        if (epoch !== generation) return;
        reset(
          "Browser worker failed. Check that the runtime can be downloaded.",
        );
      };
    } catch (error) {
      reset(error.message);
    }
    return promise;
  }
  async function readAsset(path) {
    const response = await fetch(new URL(path, BASE));
    if (!response.ok) throw Error("Example asset unavailable: " + path);
    return response;
  }
  function b64(data) {
    let text = "";
    for (let offset = 0; offset < data.length; offset += 16384)
      text += String.fromCharCode(...data.subarray(offset, offset + 16384));
    return btoa(text);
  }
  async function example(path) {
    if (path === "/api/relocation-examples") {
      const manifest = await (
        await readAsset("examples/relocation/manifest.json")
      ).json();
      async function observation(item) {
        if (
          typeof item.name !== "string" ||
          item.name.includes("/") ||
          item.name.includes("\\") ||
          item.name === ".."
        )
          throw Error("Invalid example name.");
        const data = new Uint8Array(
          await (
            await readAsset("examples/relocation/" + item.name)
          ).arrayBuffer(),
        );
        return {
          name: item.name,
          start: item.start,
          end: item.end,
          data: b64(data),
        };
      }
      return {
        description: manifest.description,
        samples: await Promise.all(manifest.training.map(observation)),
        working: await observation(manifest.working),
      };
    }
    const url = new URL(path, location.origin),
      family = url.searchParams.get("family") || "wav";
    if (!["wav", "png", "aiff"].includes(family))
      throw Error("Unknown example family.");
    const manifest = await (await readAsset("examples/manifest.json")).json();
    const samples = await Promise.all(
      manifest.training[family].map(async (item) => ({
        name: item.name,
        data: b64(
          new Uint8Array(
            await (await readAsset("examples/" + item.name)).arrayBuffer(),
          ),
        ),
      })),
    );
    return {
      samples,
      description:
        "Real FFmpeg-produced files. The Python inference engine receives only their bytes.",
    };
  }
  async function request(path, body) {
    if (
      body === undefined &&
      (path.startsWith("/api/examples?") || path === "/api/relocation-examples")
    )
      return example(path);
    const encoded = JSON.stringify(body === undefined ? {} : body);
    if (new TextEncoder().encode(encoded).length > MAX_BODY)
      throw Error("Request exceeds 12 MiB.");
    await ensureWorker();
    // Serialize operations so one selected computation owns the worker and deadline.
    if (pending.size)
      throw Error("A browser operation is already running. Wait or cancel it.");
    const id = ++nextId;
    text("Computing locally in Python…");
    if (cancelButton) cancelButton.disabled = false;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(
        () => reset("Operation exceeded its 20-second bound."),
        20000,
      );
      pending.set(id, {
        resolve,
        reject,
        timer,
        exportModelId:
          ["/api/export", "/api/relocation/export"].includes(path) &&
          /^[0-9a-f]{64}$/.test(body?.model?.id)
            ? body.model.id
            : null,
      });
      try {
        worker.postMessage({ id, path, body: JSON.parse(encoded) });
      } catch (error) {
        reset(error.message);
      }
    });
  }
  window.ReclayTransport = {
    enabled: true,
    request,
    cancel: () => reset("Operation cancelled."),
  };
  if (cancelButton)
    cancelButton.addEventListener("click", () =>
      window.ReclayTransport.cancel(),
    );
  text(
    "Browser engine loads when you start. First use downloads the pinned Python runtime.",
  );
})();
