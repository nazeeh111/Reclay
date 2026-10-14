"""Produce a source-bearing static lab using the original Python engine."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def build(destination):
    destination = Path(destination)
    # A fresh output directory prevents accidental replacement of user data.
    destination.mkdir(parents=True, exist_ok=False)
    entries = []

    def put(name, data):
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(data)
        entries.append(
            {
                "path": name,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )

    banner = """<section class="browser-runtime" aria-label="Browser engine"><span id="browser-runtime-status" role="status" aria-live="polite">Files stay in this browser.</span><button id="browser-cancel" type="button" disabled>Cancel computation</button><small>First computation downloads a pinned Python runtime from jsDelivr. No files are uploaded. <a href="https://pyodide.org/" target="_blank" rel="noopener noreferrer">Powered by Pyodide</a>.</small></section>"""
    csp = """<meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self' https://cdn.jsdelivr.net 'wasm-unsafe-eval'; connect-src 'self' https://cdn.jsdelivr.net; style-src 'self'; img-src 'self' blob: data:; worker-src 'self'; object-src 'none'; base-uri 'self'">"""
    for name in ["index.html", "relocation.html"]:
        html = (ROOT / "reclay/web" / name).read_text()
        html = html.replace("<body>", '<body data-runtime="browser">')
        html = html.replace(
            "</head>",
            csp + '<script src="./browser-transport.js" defer></script></head>',
        )
        # Deferred scripts execute in document order. Transport must precede app.
        html = html.replace(
            '<script src="/app.js" defer></script>',
            '<script src="./browser-transport.js" defer></script><script src="./app.js" defer></script>',
        )
        html = html.replace(
            '<script src="/relocation.js" defer></script>',
            '<script src="./browser-transport.js" defer></script><script src="./relocation.js" defer></script>',
        )
        # Remove the fallback injection if transport was placed before the app.
        if html.count('src="./browser-transport.js"') > 1:
            marker = csp + '<script src="./browser-transport.js" defer></script></head>'
            html = html.replace(marker, csp + "</head>")
        for filename in [
            "style.css",
            "relocation.css",
            "app.js",
            "relocation.js",
            "relocation.html",
        ]:
            html = html.replace('"/' + filename + '"', '"./' + filename + '"')
        html = html.replace('href="/"', 'href="./index.html"')
        html = html.replace("</header>", "</header>" + banner, 1)
        html = html.replace(
            "Your files stay on this machine.", "Your files stay in this browser."
        )
        html = html.replace("Local workspace", "Browser workspace")
        put(name, html.encode())
    for name in [
        "style.css",
        "relocation.css",
        "app.js",
        "relocation.js",
        "browser-transport.js",
        "browser-worker.mjs",
    ]:
        put(name, (ROOT / "reclay/web" / name).read_bytes())
    for name in [
        "__init__.py",
        "engine.py",
        "standalone.py",
        "relocation.py",
        "browser_dispatch.py",
    ]:
        put("python/" + name, (ROOT / "reclay" / name).read_bytes())
    for path in sorted((ROOT / "reclay/examples").rglob("*")):
        if path.is_file():
            put(
                "examples/" + path.relative_to(ROOT / "reclay/examples").as_posix(),
                path.read_bytes(),
            )
    put("LICENSE.txt", (ROOT / "LICENSE").read_bytes())
    put(
        "THIRD_PARTY_NOTICES.txt",
        b"""Reclay browser lab loads Pyodide314.0.7 from its officially documented jsDelivr distribution. Pyodide is developed by Pyodide contributors and Mozilla and uses MPL2.0; CPython and bundled components retain their respective upstream licenses. No third-party runtime source is copied or relicensed here. Official distribution/source/licenses: https://github.com/pyodide/pyodide/tree/314.0.7 and https://pyodide.org/en/stable/project/about.html . All original Reclay code is MIT. The included synthetic specimens were independently produced using FFmpeg and Pillow; producer provenance is recorded in their manifests. No endorsement is implied.\n""",
    )
    put(
        "README.txt",
        b"""Reclay browser laboratory\n\nServe this directory using an ordinary HTTP server, or deploy its contents as static HTTPS files. Opening file:// directly is unsupported. Both studios execute the shipped Python engine in a module Web Worker using pinned Pyodide314.0.7. First use requires internet access to jsDelivr for the Python runtime; there is no offline-first guarantee. Specimen bytes and inferred models stay in the browser and are never submitted to a computation server. Only packaged examples, source and runtime resources are fetched. Cancel terminates the worker, and a subsequent operation creates a fresh instance. Standalone Python editors exported by either studio need no Reclay installation.\n\nKnown supported model families and original validation: https://github.com/nazeeh111/Reclay . Model hashes and source hashes establish integrity identities, not authenticity, semantic completeness or universal safety.\n""",
    )
    put(
        "browser-runtime.css",
        b""".browser-runtime{max-width:1468px;margin:0 auto;padding:12px 24px;display:flex;align-items:center;gap:16px;flex-wrap:wrap;background:#e2e8dc;border-bottom:1px solid #c7cdbf;font:12px/1.5 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif}.browser-runtime button{padding:7px 12px;border:1px solid #b1bead;border-radius:4px;background:#f5f7ef;color:#344e3b;font:inherit;cursor:pointer}.browser-runtime button:disabled{opacity:.45;cursor:default}.browser-runtime small{flex-basis:100%;font-size:10px;color:#536253}.browser-runtime a{color:inherit}""",
    )
    # Stylesheet is source-bearing and loaded in both transformed documents.
    for name in ["index.html", "relocation.html"]:
        path = destination / name
        data = path.read_bytes().replace(
            b"</head>", b'<link rel="stylesheet" href="./browser-runtime.css"></head>'
        )
        path.write_bytes(data)
        row = next(x for x in entries if x["path"] == name)
        row.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    manifest = {
        "version": 1,
        "pyodideVersion": "314.0.7",
        "runtimeBase": "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/",
        "files": entries,
    }
    with (destination / "browser-manifest.json").open("x") as stream:
        json.dump(manifest, stream, indent=2)
        stream.write("\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    manifest = build(args.out)
    print(
        json.dumps(
            {
                "out": args.out,
                "files": len(manifest["files"]),
                "pyodide": manifest["pyodideVersion"],
            }
        )
    )
