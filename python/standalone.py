"""Export a stdlib-only inferred codec; no input is executed as code."""

import json
from pathlib import Path
from .engine import validate_model


def export_standalone(model):
    validate_model(model)
    core = Path(__file__).with_name("engine.py").read_text(encoding="utf-8")
    encoded = json.dumps(
        model, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return _LICENSE + core + "\n\nMODEL = json.loads(" + repr(encoded) + ")\n" + _MAIN


_LICENSE = """# MIT License
#
# Copyright (c) 2026 Nazeeh Abdul-Hadi
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""


_MAIN = r"""
def main(argv=None):
    import argparse
    import sys
    from pathlib import Path
    parser=argparse.ArgumentParser(description="Standalone lossless codec inferred by Reclay. Raw payload editing only; no format-name lookup.")
    commands=parser.add_subparsers(dest="command",required=True)
    inspect=commands.add_parser("inspect")
    inspect.add_argument("file")
    edit=commands.add_parser("rebuild")
    edit.add_argument("file")
    edit.add_argument("edits",help="JSON list of {path:[index,...],payloadHex:...}")
    edit.add_argument("output")
    args=parser.parse_args(argv)
    try:
        source=Path(args.file)
        with source.open("rb") as stream:
            data=stream.read(MAX_FILE+1)
        document=parse(data,MODEL)
        if args.command=="inspect":
            print(json.dumps(document,indent=2));return 0
        edit_path=Path(args.edits)
        with edit_path.open("rb") as stream:
            edit_bytes=stream.read(3*MAX_FILE+1)
        if len(edit_bytes)>3*MAX_FILE:
            raise ReclayError("input-bound","Edits JSON exceeds 3 MiB")
        edits=json.loads(edit_bytes.decode("utf-8"))
        result=rebuild(document,MODEL,edits)
        # Exclusive creation prevents accidental overwrite of original/user data.
        with Path(args.output).open("xb") as target:
            target.write(result)
        print(json.dumps({"output":str(Path(args.output)),"bytes":len(result),"sha256":hashlib.sha256(result).hexdigest()}))
        return 0
    except ReclayError as error:
        print(json.dumps({"error":error.as_dict()}),file=sys.stderr);return 2
    except (OSError,UnicodeError,json.JSONDecodeError,RecursionError) as error:
        print(json.dumps({"error":{"code":"io-or-json","message":str(error)}}),file=sys.stderr);return 2

if __name__=="__main__":
    raise SystemExit(main())
"""
