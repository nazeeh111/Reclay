"""Static delivery preserves authoritative source and refuses reused destinations."""

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "browser_builder", ROOT / "scripts/build_browser_lab.py"
)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class BrowserBuild(unittest.TestCase):
    def test_actual_manifest_sources_examples_and_relative_pages(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "lab"
            manifest = builder.build(out)
            self.assertEqual(manifest["pyodideVersion"], "314.0.7")
            self.assertEqual(
                len(manifest["files"]), len({x["path"] for x in manifest["files"]})
            )
            for row in manifest["files"]:
                data = (out / row["path"]).read_bytes()
                self.assertEqual(row["bytes"], len(data))
                self.assertEqual(row["sha256"], hashlib.sha256(data).hexdigest())
            for name in (
                "__init__.py",
                "engine.py",
                "standalone.py",
                "relocation.py",
                "browser_dispatch.py",
            ):
                self.assertEqual(
                    (out / "python" / name).read_bytes(),
                    (ROOT / "reclay" / name).read_bytes(),
                )
            for name, script in (
                ("index.html", "app.js"),
                ("relocation.html", "relocation.js"),
            ):
                html = (out / name).read_text()
                self.assertIn('data-runtime="browser"', html)
                self.assertEqual(html.count('src="./browser-transport.js"'), 1)
                self.assertLess(
                    html.index('src="./browser-transport.js"'),
                    html.index('src="./' + script + '"'),
                )
                self.assertNotIn('href="/"', html)
                self.assertIn("wasm-unsafe-eval", html)
                self.assertIn("Cancel computation", html)
            self.assertEqual(
                (out / "examples/relocation/working.tif").read_bytes(),
                (ROOT / "reclay/examples/relocation/working.tif").read_bytes(),
            )
            original = (out / "index.html").read_bytes()
            with self.assertRaises(FileExistsError):
                builder.build(out)
            self.assertEqual((out / "index.html").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
