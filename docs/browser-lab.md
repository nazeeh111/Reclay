# Browser-only laboratory

Both studios run the authoritative Python block and relocation engines inside a module Web Worker using pinned Pyodide 314.0.7. The browser dispatcher exposes only the declared data operations; specimen bytes and models are never sent to a computation server. The same source files are hash-checked against the shipped manifest before being mounted in the browser runtime. These hashes identify the shipped content; they do not authenticate an unknown host or prove engine safety.

First computation fetches the runtime from the officially documented jsDelivr distribution. This requires network access and is not an offline-first guarantee. The page fetches packaged examples and source files from its own host. Pyodide/CPython and their upstream contributors are credited in the static lab's notices. No model API, account or paid service is required.

Cancel terminates the current worker and rejects its pending operation. A subsequent operation initializes a new worker. Runtime startup is bounded to 90 seconds, and each operation to 20 seconds; startup/network errors remain visible. The retained input collection and drafts stay in the page. Cancellation revokes prior download previews; it does not delete user files.

The browser supports the existing evidence-bounded tagged-block and marked-span relocation families. Original format-semantic limits, ambiguity, truncation, integer bounds and invariant envelope guards remain. Browser hosting does not turn a matched document family into arbitrary-format support. Standalone editor downloads still use ordinary Python 3.10+ without a browser or Reclay installation.

Build into a new destination:

```sh
python scripts/build_browser_lab.py --out /tmp/reclay-browser-new
python -m http.server --bind 127.0.0.1 --directory /tmp/reclay-browser-new
```

Use localhost HTTP or HTTPS static hosting. Opening file:// is unsupported. Generated pages use relative paths and work under a repository prefix. The lab includes its exact engine source, examples, manifest, license and dependency notices.

Actual browser checks reproduced a TIFF resize byte-for-byte against a separate Pillow output and a nested WAVE title edit against a separate FFmpeg output. All 40,003 WAVE audio samples remained unchanged. A separately reviewed browser session checked cancellation, fresh-worker restart, exported-editor execution with Python site packages disabled, refusal of a below-observed-size edit, and rejection of changed packaged source. Controlled Node worker tests cover late messages and deadlines separately; they are not interpreter replication.

Exported editors contain plain comments with the project URL and selected model hash. They contain no telemetry or automatic network behavior.
