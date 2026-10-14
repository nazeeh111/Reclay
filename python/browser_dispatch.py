"""Data-only browser RPCs around the unchanged trusted Python engines.

The worker invokes one fixed function, not client Python code. Browser request
timeouts and cancellation terminate that worker; no server process is needed.
"""

import base64
import hashlib
import json

from . import __version__
from .engine import infer, parse, rebuild
from .relocation import (
    infer_relocation,
    inspect_relocation,
    resize_relocation,
    export_relocation,
)
from .standalone import export_standalone

MAX_FILE = 1_048_576
MAX_JSON = 12 * MAX_FILE
ROUTES = {
    "/api/health": (set(), set()),
    "/health": (set(), set()),
    "/api/infer": ({"samples"}, set()),
    "/api/parse": ({"data", "model"}, set()),
    "/api/edit": ({"data", "model"}, {"edits"}),
    "/api/export": ({"model"}, set()),
    "/api/relocation/infer": ({"samples"}, set()),
    "/api/relocation/inspect": ({"data", "model", "start", "end"}, set()),
    "/api/relocation/resize": (
        {"data", "model", "start", "end", "replacement"},
        set(),
    ),
    "/api/relocation/export": ({"model"}, set()),
}


class BrowserDispatchError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code

    def as_dict(self):
        return {"code": self.code, "message": str(self), "context": {}}


def _fail(code, message):
    raise BrowserDispatchError(code, message)


def _json(value):
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    if len(encoded.encode("utf-8")) > MAX_JSON:
        _fail("json-bound", "JSON exceeds the 12 MiB browser boundary")
    return encoded


def _decode(value):
    if type(value) is not str or len(value) > ((MAX_FILE + 2) // 3) * 4:
        _fail("input-bound", "Binary data must be bounded base64 text, at most 1 MiB")
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, UnicodeError) as error:
        _fail("invalid-base64", str(error))
    if len(data) > MAX_FILE:
        _fail("input-bound", "Decoded binary input exceeds 1 MiB")
    return data


def _observations(value, relocation=False):
    if type(value) is not list or not 2 <= len(value) <= 8:
        _fail("input-bound", "Provide 2–8 observations")
    blobs, spans = [], []
    for observation in value:
        if type(observation) is not dict:
            _fail("invalid-observation", "Each observation must be an object")
        required = {"data", "start", "end"} if relocation else {"data"}
        if not required <= set(observation) or set(observation) - required - {"name"}:
            _fail(
                "invalid-observation", "Observation has unknown or missing properties"
            )
        blobs.append(_decode(observation["data"]))
        if relocation:
            spans.append((observation["start"], observation["end"]))
    if sum(map(len, blobs)) > 4 * MAX_FILE:
        _fail("input-bound", "Observation corpus exceeds 4 MiB")
    return blobs, spans


def dispatch(path, body):
    """Return the ordinary server-compatible result; raise bounded input errors."""
    if type(path) is not str or path not in ROUTES:
        _fail("unknown-operation", "Unknown browser operation")
    if type(body) is not dict:
        _fail("invalid-input", "Request body must be a JSON object")
    _json(body)
    required, optional = ROUTES[path]
    if not required <= set(body) or set(body) - required - optional:
        _fail("invalid-input", "Request has unknown or missing properties")
    if path in ("/api/health", "/health"):
        result = {"version": __version__, "browser": True}
    elif path in ("/api/infer", "/api/relocation/infer"):
        relocation = path == "/api/relocation/infer"
        data, spans = _observations(body["samples"], relocation)
        result = infer_relocation(data, spans) if relocation else infer(data)
    elif path in ("/api/export", "/api/relocation/export"):
        relocation = path == "/api/relocation/export"
        result = {
            "filename": "relocation-editor.py" if relocation else "codec.py",
            "source": export_relocation(body["model"])
            if relocation
            else export_standalone(body["model"]),
        }
    elif path in ("/api/relocation/inspect", "/api/relocation/resize"):
        data = _decode(body["data"])
        if path.endswith("/inspect"):
            result = inspect_relocation(data, body["model"], body["start"], body["end"])
        else:
            output, receipt = resize_relocation(
                data,
                body["model"],
                body["start"],
                body["end"],
                _decode(body["replacement"]),
            )
            result = {"data": base64.b64encode(output).decode(), "changes": receipt}
    else:
        data = _decode(body["data"])
        document = parse(data, body["model"])
        if path == "/api/parse":
            result = document
        else:
            edits = body.get("edits", [])
            if type(edits) is not list or len(edits) > 64:
                _fail("input-bound", "At most 64 payload edits are allowed")
            output = rebuild(document, body["model"], edits)
            changes = {
                "originalBytes": len(data),
                "editedBytes": len(output),
                "changedBytes": sum(a != b for a, b in zip(data, output))
                + abs(len(data) - len(output)),
                "sha256": hashlib.sha256(output).hexdigest(),
                "note": "Changed bytes compare equal file offsets; moved payloads also contribute. This is not a minimal edit distance.",
            }
            result = {
                "data": base64.b64encode(output).decode(),
                "document": parse(output, body["model"]),
                "changes": changes,
            }
    _json(result)
    return result


def dispatch_json(path, body_json):
    """Fixed Pyodide entrypoint. Specimen strings remain data throughout."""
    try:
        if type(body_json) is not str or len(body_json.encode("utf-8")) > MAX_JSON:
            _fail("json-bound", "Request JSON exceeds 12 MiB")

        def invalid_constant(value):
            _fail("invalid-json", "Non-finite JSON numbers are not supported")

        body = json.loads(body_json, parse_constant=invalid_constant)
        return _json({"ok": True, "result": dispatch(path, body)})
    except (
        ValueError,
        KeyError,
        TypeError,
        UnicodeError,
        OverflowError,
        RecursionError,
        OSError,
    ) as error:
        return _json(
            {
                "ok": False,
                "error": {
                    "code": getattr(error, "code", "invalid-input"),
                    "message": str(error)[:2048],
                    "context": {},
                },
            }
        )
