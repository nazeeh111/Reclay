"""Verify the installed payload and two actual portable editing workflows."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import reclay
from reclay.engine import infer, parse, rebuild
from reclay.relocation import infer_relocation, resize_relocation, export_relocation
from reclay.standalone import export_standalone

source = Path(__file__).resolve().parents[1] / "reclay"
installed = Path(reclay.__file__).resolve().parent
assert installed.is_relative_to(Path(sys.prefix).resolve()), (installed, sys.prefix)
assert installed != source
count = 0
for item in source.rglob("*"):
    if item.is_file() and "__pycache__" not in item.parts and item.suffix != ".pyc":
        actual = installed / item.relative_to(source)
        assert actual.read_bytes() == item.read_bytes(), str(actual)
        count += 1
examples = installed / "examples"
legacy = infer(
    [
        (examples / n).read_bytes()
        for n in ("river.wav", "riverbend.wav", "skyline-observation.wav")
    ]
)
old_model = next(m for m in legacy["models"] if m["prefix"]["growthSafe"])
old_data = (examples / "river.wav").read_bytes()
assert rebuild(parse(old_data, old_model), old_model) == old_data
folder = examples / "relocation"
manifest = json.loads((folder / "manifest.json").read_text())
training = manifest["training"]
answer = infer_relocation(
    [(folder / x["name"]).read_bytes() for x in training],
    [[x["start"], x["end"]] for x in training],
)
assert answer["status"] == "unique" and not answer["truncated"]
model = answer["models"][0]
working = manifest["working"]
data = (folder / working["name"]).read_bytes()
for case in manifest["heldout"]:
    payload = (folder / case["payload"]).read_bytes()
    output, receipt = resize_relocation(
        data, model, working["start"], working["end"], payload
    )
    assert output == (folder / case["oracle"]).read_bytes()
with tempfile.TemporaryDirectory(prefix="reclay-detached-") as temp:
    p = Path(temp)
    editor = p / "editor.py"
    editor.write_text(export_relocation(model))
    input_file = p / "input.bin"
    input_file.write_bytes(data)
    case = manifest["heldout"][1]
    payload_file = p / "payload.bin"
    payload_file.write_bytes((folder / case["payload"]).read_bytes())
    result_file = p / "edited.bin"
    run = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(editor),
            "rebuild",
            str(input_file),
            str(payload_file),
            str(result_file),
            "--start",
            str(working["start"]),
            "--end",
            str(working["end"]),
        ],
        cwd=p,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert run.returncode == 0, run.stderr
    assert result_file.read_bytes() == (folder / case["oracle"]).read_bytes()
    # Also exercise the old standalone codec with site packages disabled.
    old_editor = p / "blocks.py"
    old_editor.write_text(export_standalone(old_model))
    old_input = p / "old.bin"
    old_input.write_bytes(old_data)
    edits = p / "edits.json"
    edits.write_text("[]")
    old_output = p / "old-output.bin"
    run = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(old_editor),
            "rebuild",
            str(old_input),
            str(edits),
            str(old_output),
        ],
        cwd=p,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert run.returncode == 0, run.stderr
    assert old_output.read_bytes() == old_data
print(
    json.dumps(
        {
            "version": reclay.__version__,
            "installed": str(installed),
            "sourceExactFiles": count,
            "relocationModels": len(answer["models"]),
            "independentFixtureMatches": len(manifest["heldout"]),
            "portableSitePackagesDisabled": True,
            "legacyStandalonePreserved": True,
        }
    )
)
