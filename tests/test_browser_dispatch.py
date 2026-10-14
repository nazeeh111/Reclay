import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from reclay.browser_dispatch import dispatch, dispatch_json, BrowserDispatchError

ROOT = Path(__file__).resolve().parents[1]
EX = ROOT / "reclay/examples"
MANIFEST = json.loads((EX / "manifest.json").read_text())
RELOCATION = json.loads((EX / "relocation/manifest.json").read_text())


def b64(data):
    return base64.b64encode(data).decode()


def walk(chunks, path=()):
    for i, chunk in enumerate(chunks):
        current = path + (i,)
        yield current, chunk
        if chunk.get("children"):
            yield from walk(chunk["children"], current)


class BrowserDispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chunks = {}
        for family in ("wav", "aiff", "png"):
            samples = [
                {"name": row["name"], "data": b64((EX / row["name"]).read_bytes())}
                for row in MANIFEST["training"][family]
            ]
            learned = dispatch("/api/infer", {"samples": samples})
            models = [
                model for model in learned["models"] if model["prefix"]["growthSafe"]
            ]
            if len(models) != 1:
                raise AssertionError(
                    "Corpus no longer supplies its single complete chunk model"
                )
            cls.chunks[family] = models[0]
        samples = [
            {
                "name": row["name"],
                "start": row["start"],
                "end": row["end"],
                "data": b64((EX / "relocation" / row["name"]).read_bytes()),
            }
            for row in RELOCATION["training"]
        ]
        cls.relocation = dispatch("/api/relocation/infer", {"samples": samples})
        cls.model = cls.relocation["models"][0]
        row = RELOCATION["working"]
        cls.working = {
            "data": b64((EX / "relocation" / row["name"]).read_bytes()),
            "model": cls.model,
            "start": row["start"],
            "end": row["end"],
        }

    def test_health_and_fixed_json_entrypoint(self):
        health = dispatch("/api/health", {})
        self.assertTrue(health["browser"])
        self.assertIsInstance(health["version"], str)
        answer = json.loads(dispatch_json("/api/health", "{}"))
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["result"], health)

    def test_chunk_title_resize_matches_separate_ffmpeg_producer(self):
        for family in ("wav", "aiff"):
            source = (EX / MANIFEST["training"][family][0]["name"]).read_bytes()
            oracle = (EX / MANIFEST["heldOut"][family][0]["name"]).read_bytes()
            model = self.chunks[family]
            doc = dispatch("/api/parse", {"data": b64(source), "model": model})
            candidates = [
                (path, chunk)
                for path, chunk in walk(doc["chunks"])
                if bytes.fromhex(chunk["payloadHex"]).rstrip(b"\0") == b"river"
            ]
            self.assertEqual(len(candidates), 1)
            path, chunk = candidates[0]
            title = MANIFEST["heldOut"][family][0]["title"].encode()
            replacement = title + (
                b"\0" if bytes.fromhex(chunk["payloadHex"]).endswith(b"\0") else b""
            )
            answer = dispatch(
                "/api/edit",
                {
                    "data": b64(source),
                    "model": model,
                    "edits": [{"path": list(path), "payloadHex": replacement.hex()}],
                },
            )
            self.assertEqual(base64.b64decode(answer["data"]), oracle)
            self.assertEqual(answer["changes"]["editedBytes"], len(oracle))
            self.assertTrue(answer["document"]["chunks"])

    def test_png_noop_retains_original_independent_fixture(self):
        row = MANIFEST["heldOut"]["png"][0]
        data = (EX / row["name"]).read_bytes()
        result = dispatch(
            "/api/edit", {"data": b64(data), "model": self.chunks["png"], "edits": []}
        )
        self.assertEqual(base64.b64decode(result["data"]), data)
        self.assertEqual(result["changes"]["changedBytes"], 0)

    def test_relocation_fresh_oracles_and_inspection_are_exact(self):
        self.assertEqual(self.relocation["status"], "unique")
        self.assertFalse(self.relocation["truncated"])
        inspected = dispatch("/api/relocation/inspect", self.working)
        self.assertEqual(inspected["minimumLength"], 5)
        self.assertEqual(len(inspected["fields"]), 3)
        for row in RELOCATION["heldout"]:
            payload = (EX / "relocation" / row["payload"]).read_bytes()
            oracle = (EX / "relocation" / row["oracle"]).read_bytes()
            result = dispatch(
                "/api/relocation/resize", dict(self.working, replacement=b64(payload))
            )
            self.assertEqual(base64.b64decode(result["data"]), oracle)
            self.assertTrue(result["changes"]["unchangedEnvelopeVerified"])
        refused = json.loads(
            dispatch_json(
                "/api/relocation/resize",
                json.dumps(dict(self.working, replacement=b64(b"abc\0"))),
            )
        )
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["error"]["code"], "minimum-length")

    def test_exports_are_actual_standalone_source_in_both_workflows(self):
        for path, model in (
            ("/api/export", self.chunks["wav"]),
            ("/api/relocation/export", self.model),
        ):
            exported = dispatch(path, {"model": model})
            self.assertIn("MIT License", exported["source"])
            with tempfile.TemporaryDirectory() as directory:
                codec = Path(directory) / exported["filename"]
                codec.write_text(exported["source"])
                if path == "/api/export":
                    data = (EX / MANIFEST["training"]["wav"][0]["name"]).read_bytes()
                    original = Path(directory) / "input.bin"
                    original.write_bytes(data)
                    args = ["inspect", str(original)]
                else:
                    original = Path(directory) / "input.bin"
                    original.write_bytes(base64.b64decode(self.working["data"]))
                    args = [
                        "inspect",
                        str(original),
                        "--start",
                        str(self.working["start"]),
                        "--end",
                        str(self.working["end"]),
                    ]
                ran = subprocess.run(
                    [sys.executable, "-I", "-S", str(codec)] + args,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(ran.returncode, 0, ran.stderr)
                self.assertIsInstance(json.loads(ran.stdout), dict)

    def test_unknown_paths_and_client_code_are_refused(self):
        for route in (
            "/api/run",
            "/api/import",
            "/api/infer?python=pass",
            "../engine.py",
            "__import__",
        ):
            answer = json.loads(dispatch_json(route, "{}"))
            self.assertFalse(answer["ok"])
            self.assertEqual(answer["error"]["code"], "unknown-operation")
        answer = json.loads(
            dispatch_json("/api/health", '{"python":"raise RuntimeError(123)"}')
        )
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["error"]["code"], "invalid-input")

    def test_json_binary_corpus_edit_and_model_bounds(self):
        for text in ("null", "[]", "NaN", '{"samples":[NaN,NaN]}', "{"):
            answer = json.loads(dispatch_json("/api/infer", text))
            self.assertFalse(answer["ok"])
            self.assertIsInstance(answer["error"]["message"], str)
        for encoded in ("not base64!", "é", b64(b"x" * (1048576 + 1))):
            answer = json.loads(
                dispatch_json(
                    "/api/parse",
                    json.dumps({"data": encoded, "model": self.chunks["wav"]}),
                )
            )
            self.assertFalse(answer["ok"])
        with self.assertRaises(BrowserDispatchError):
            dispatch("/api/infer", {"samples": [{"data": b64(b"x" * 1048576)}] * 5})
        with self.assertRaises(BrowserDispatchError):
            dispatch(
                "/api/edit",
                {
                    "data": b64(
                        (EX / MANIFEST["training"]["wav"][0]["name"]).read_bytes()
                    ),
                    "model": self.chunks["wav"],
                    "edits": [{}] * 65,
                },
            )
        answer = json.loads(
            dispatch_json("/api/relocation/export", '{"model":{"kind":"python"}}')
        )
        self.assertFalse(answer["ok"])
        answer = json.loads(dispatch_json("/api/health", " " * (12 * 1048576 + 1)))
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["error"]["code"], "json-bound")


if __name__ == "__main__":
    unittest.main()
