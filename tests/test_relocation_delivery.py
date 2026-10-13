"""Independent producer fixtures cross the actual HTTP and CLI boundaries."""

import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from reclay.relocation import infer_relocation
from reclay.server import Handler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
EX = ROOT / "reclay/examples/relocation"


class RelocationDelivery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((EX / "manifest.json").read_text())
        cls.samples = [
            {**x, "data": base64.b64encode((EX / x["name"]).read_bytes()).decode()}
            for x in cls.manifest["training"]
        ]
        cls.answer = infer_relocation(
            [(EX / x["name"]).read_bytes() for x in cls.manifest["training"]],
            [[x["start"], x["end"]] for x in cls.manifest["training"]],
        )
        cls.model = cls.answer["models"][0]
        cls.working = cls.manifest["working"]

    def test_actual_http_inference_edit_export_and_guards(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = "http://127.0.0.1:" + str(server.server_port)

        def request(path, obj=None, origin=None):
            headers = {"Content-Type": "application/json"}
            if origin:
                headers["Origin"] = origin
            req = urllib.request.Request(
                base + path,
                data=None if obj is None else json.dumps(obj).encode(),
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=25) as response:
                return response.read()

        try:
            samples = json.loads(request("/api/relocation-examples"))
            self.assertEqual(len(samples["samples"]), len(self.samples))
            model = json.loads(
                request("/api/relocation/infer", {"samples": samples["samples"]})
            )["models"][0]
            self.assertEqual(model["id"], self.model["id"])
            working = samples["working"]
            body = {
                "model": model,
                "data": working["data"],
                "start": working["start"],
                "end": working["end"],
            }
            doc = json.loads(request("/api/relocation/inspect", body))
            self.assertEqual(len(doc["fields"]), 3)
            for case in self.manifest["heldout"]:
                answer = json.loads(
                    request(
                        "/api/relocation/resize",
                        {
                            **body,
                            "replacement": base64.b64encode(
                                (EX / case["payload"]).read_bytes()
                            ).decode(),
                        },
                    )
                )
                self.assertEqual(
                    base64.b64decode(answer["data"]), (EX / case["oracle"]).read_bytes()
                )
                self.assertTrue(answer["changes"]["unchangedEnvelopeVerified"])
            exported = json.loads(request("/api/relocation/export", {"model": model}))
            compile(exported["source"], "portable.py", "exec")
            for path in ("/relocation.html", "/relocation.css", "/relocation.js", "/"):
                self.assertTrue(request(path))
            bad = bytearray(base64.b64decode(body["data"]))
            bad[-1] ^= 1
            for obj, origin in [
                ({**body, "data": base64.b64encode(bad).decode()}, None),
                (body, "https://example.org"),
            ]:
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    request("/api/relocation/inspect", obj, origin)
                self.assertEqual(raised.exception.code, 400)
                raised.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_cli_detached_editor_and_no_clobber(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            schema = p / "schema.json"
            schema.write_text(json.dumps(self.answer))
            w = self.working
            source = EX / w["name"]
            case = self.manifest["heldout"][1]

            def run(*args):
                return subprocess.run(
                    [sys.executable, "-m", "reclay", *args],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )

            output = p / "edited.bin"
            result = run(
                "relocate",
                "--schema",
                str(schema),
                str(source),
                "--start",
                str(w["start"]),
                "--end",
                str(w["end"]),
                "--payload",
                str(EX / case["payload"]),
                "--out",
                str(output),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_bytes(), (EX / case["oracle"]).read_bytes())
            before = output.read_bytes()
            again = run(
                "relocate",
                "--schema",
                str(schema),
                str(source),
                "--start",
                str(w["start"]),
                "--end",
                str(w["end"]),
                "--payload",
                str(EX / case["payload"]),
                "--out",
                str(output),
            )
            self.assertEqual(again.returncode, 2)
            self.assertEqual(output.read_bytes(), before)
            truncated = dict(self.answer, truncated=True)
            schema.write_text(json.dumps(truncated))
            result = run(
                "inspect-relocation",
                "--schema",
                str(schema),
                str(source),
                "--start",
                str(w["start"]),
                "--end",
                str(w["end"]),
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("ambiguous", result.stderr.lower())
            result = run(
                "inspect-relocation",
                "--schema",
                str(schema),
                "--model",
                self.model["id"],
                str(source),
                "--start",
                str(w["start"]),
                "--end",
                str(w["end"]),
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
