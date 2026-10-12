"""Loopback-only studio. No path-reading or command-running RPCs."""

import base64, hashlib, json, multiprocessing, pathlib, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
from . import __version__
from .engine import infer, parse, rebuild
from .standalone import export_standalone

MAX_FILE = 1_048_576
MAX_BODY = 12 * MAX_FILE
ROOT = pathlib.Path(__file__).resolve().parent
INFER_LOCK = threading.BoundedSemaphore(1)


def decode(value):
    if not isinstance(value, str) or len(value) > ((MAX_FILE + 2) // 3) * 4:
        raise ValueError("Binary data exceeds 1 MiB or is not base64 text")
    data = base64.b64decode(value, validate=True)
    if len(data) > MAX_FILE:
        raise ValueError("Binary input exceeds 1 MiB")
    return data


def _infer_process(data, conn):
    try:
        conn.send({"ok": True, "result": infer(data)})
    except Exception as e:
        conn.send(
            {
                "ok": False,
                "error": str(e),
                "code": getattr(e, "code", "inference-failed"),
            }
        )
    finally:
        conn.close()


def bounded_infer(samples):
    if not isinstance(samples, list) or not 2 <= len(samples) <= 8:
        raise ValueError("Provide between 2 and 8 observations")
    data = [decode(x["data"]) for x in samples]
    if sum(map(len, data)) > 4 * MAX_FILE:
        raise ValueError("Corpus exceeds 4 MiB")
    if not INFER_LOCK.acquire(blocking=False):
        raise ValueError("An inference is running. Try again after it finishes")
    process = None
    parent = None
    try:
        ctx = multiprocessing.get_context("spawn")
        parent, child = ctx.Pipe(duplex=False)
        process = ctx.Process(target=_infer_process, args=(data, child))
        process.start()
        child.close()
        if not parent.poll(20):
            raise ValueError(
                "Inference exceeded its 20-second bound. Use smaller or more discriminating observations"
            )
        answer = parent.recv()
        if not answer["ok"]:
            raise ValueError(answer["error"])
        return answer["result"]
    finally:
        if process:
            process.join(timeout=0.2)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
        if parent:
            parent.close()
        INFER_LOCK.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "Reclay/" + __version__

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *args):
        pass

    def send(self, status, data, ctype="application/json"):
        blob = json.dumps(data).encode() if ctype == "application/json" else data
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(blob)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; connect-src 'self'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(blob)

    def gate(self):
        allowed = {
            f"127.0.0.1:{self.server.server_port}",
            f"localhost:{self.server.server_port}",
        }
        if self.headers.get("Host") not in allowed:
            raise ValueError("Use the printed loopback address")
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + h for h in allowed}:
            raise ValueError("Cross-origin requests are rejected")

    def do_GET(self):
        try:
            self.gate()
            url = urlsplit(self.path)
            if url.path == "/api/health":
                return self.send(200, {"version": __version__})
            if url.path == "/api/examples":
                family = parse_qs(url.query).get("family", ["wav"])[0]
                manifest = json.loads((ROOT / "examples/manifest.json").read_text())
                if family not in ["wav", "aiff", "png"]:
                    raise ValueError("Unknown example family")
                samples = [
                    {
                        "name": x["name"],
                        "data": base64.b64encode(
                            (ROOT / "examples" / x["name"]).read_bytes()
                        ).decode(),
                    }
                    for x in manifest["training"][family]
                ]
                return self.send(
                    200,
                    {
                        "samples": samples,
                        "description": "Real FFmpeg outputs. The inference engine receives only the bytes, with no extension, format name or specification.",
                    },
                )
            assets = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/style.css": ("style.css", "text/css; charset=utf-8"),
            }
            if url.path not in assets:
                return self.send(404, {"error": "Not found"})
            filename, ctype = assets[url.path]
            return self.send(200, (ROOT / "web" / filename).read_bytes(), ctype)
        except (ValueError, OSError, KeyError, TypeError, RecursionError) as e:
            return self.send(400, {"error": str(e)})

    def do_POST(self):
        try:
            self.gate()
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("POST requires application/json")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                raise ValueError("Request exceeds the body limit or is empty")
            obj = json.loads(self.rfile.read(length))
            if not isinstance(obj, dict):
                raise ValueError("Request must be a JSON object")
            if self.path == "/api/infer":
                return self.send(200, bounded_infer(obj["samples"]))
            if self.path == "/api/export":
                return self.send(
                    200,
                    {"filename": "codec.py", "source": export_standalone(obj["model"])},
                )
            data = decode(obj["data"])
            model = obj["model"]
            doc = parse(data, model)
            if self.path == "/api/parse":
                return self.send(200, doc)
            if self.path == "/api/edit":
                edits = obj.get("edits", [])
                if not isinstance(edits, list) or len(edits) > 64:
                    raise ValueError("At most 64 payload edits are allowed")
                output = rebuild(doc, model, edits)
                changes = {
                    "originalBytes": len(data),
                    "editedBytes": len(output),
                    "changedBytes": sum(a != b for a, b in zip(data, output))
                    + abs(len(data) - len(output)),
                    "sha256": hashlib.sha256(output).hexdigest(),
                    "note": "Changed bytes compare equal file offsets; moved payloads also contribute. This is not a minimal edit distance.",
                }
                return self.send(
                    200,
                    {
                        "data": base64.b64encode(output).decode(),
                        "document": parse(output, model),
                        "changes": changes,
                    },
                )
            return self.send(404, {"error": "Unknown operation"})
        except (
            ValueError,
            OSError,
            KeyError,
            TypeError,
            OverflowError,
            RecursionError,
            EOFError,
        ) as e:
            return self.send(
                400, {"error": str(e), "code": getattr(e, "code", "invalid-input")}
            )


def serve(port=0):
    if not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("Port must be 0..65535")
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    print(f"Reclay {__version__} · http://127.0.0.1:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
