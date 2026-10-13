"""Local CLI. Inputs are data; no inferred file or schema is executed."""

import argparse, json, pathlib, sys
from . import __version__
from .engine import infer, parse, rebuild, ReclayError
from .standalone import export_standalone
from .relocation import (
    infer_relocation,
    inspect_relocation,
    resize_relocation,
    export_relocation,
)


def read(path, max_bytes=1_048_576):
    p = pathlib.Path(path)
    if p.stat().st_size > max_bytes:
        raise ValueError("File exceeds the input size limit")
    with p.open("rb") as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("File exceeds the input size limit")
    return data


def save(path, data):
    p = pathlib.Path(path)
    # Create exclusively: a failed command never replaces an existing file.
    with p.open("xb") as f:
        try:
            f.write(data)
        except BaseException:
            f.close()
            p.unlink()
            raise


def choose(path, model_id=None):
    obj = json.loads(read(path, 9 * 1_048_576))
    models = (
        (
            [obj]
            if "id" in obj and ("grammar" in obj or obj.get("kind") == "relocation")
            else obj.get("models", [])
        )
        if isinstance(obj, dict)
        else []
    )
    if not models:
        raise ValueError("Schema contains no inferred models")
    if model_id:
        matches = [m for m in models if m.get("id", m.get("modelId")) == model_id]
        if len(matches) != 1:
            raise ValueError("Model ID is absent or duplicated")
        return matches[0]
    if len(models) != 1 or (isinstance(obj, dict) and obj.get("truncated") is True):
        raise ValueError(
            "Schema is ambiguous. Inspect its models and select --model ID explicitly"
        )
    return models[0]


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="reclay",
        description="Learn binary chunk rules from a corpus, then compile an editor.",
    )
    p.add_argument("--version", action="version", version=__version__)
    sp = p.add_subparsers(dest="command", required=True)
    q = sp.add_parser("infer")
    q.add_argument("files", nargs="+")
    q.add_argument("--out", required=True)
    for action in ["inspect", "edit", "export"]:
        q = sp.add_parser(action)
        q.add_argument("--schema", required=True)
        q.add_argument("--model")
        if action != "export":
            q.add_argument("file")
        if action in ["edit", "export"]:
            q.add_argument("--out", required=True)
        if action == "edit":
            q.add_argument(
                "--path", required=True, help="Zero-based chunk path, e.g. 1/0"
            )
            g = q.add_mutually_exclusive_group(required=True)
            g.add_argument("--hex")
            g.add_argument("--payload", help="Replacement payload file")
    q = sp.add_parser(
        "infer-relocation", help="Learn from explicit matching span annotations"
    )
    q.add_argument(
        "observations",
        help="JSON list of {file,start,end}; relative paths resolve beside this JSON",
    )
    q.add_argument("--out", required=True)
    for action in ["inspect-relocation", "relocate", "export-relocation"]:
        q = sp.add_parser(action)
        q.add_argument("--schema", required=True)
        q.add_argument("--model")
        if action != "export-relocation":
            q.add_argument("file")
            q.add_argument("--start", type=int, required=True)
            q.add_argument("--end", type=int, required=True)
        if action != "inspect-relocation":
            q.add_argument("--out", required=True)
        if action == "relocate":
            q.add_argument("--payload", required=True)
    q = sp.add_parser("serve")
    q.add_argument("--port", type=int, default=0)
    a = p.parse_args(argv)
    try:
        if a.command == "infer-relocation":
            observations = json.loads(read(a.observations))
            if not isinstance(observations, list) or not 2 <= len(observations) <= 8:
                raise ValueError("Provide 2 to 8 explicit file/span observations")
            folder = pathlib.Path(a.observations).resolve().parent
            data, spans = [], []
            for item in observations:
                if not isinstance(item, dict) or set(item) != {"file", "start", "end"}:
                    raise ValueError(
                        "Each observation needs exactly file, start and end"
                    )
                data.append(read(folder / item["file"]))
                spans.append([item["start"], item["end"]])
            result = infer_relocation(data, spans)
            save(a.out, (json.dumps(result, indent=2) + "\n").encode())
            print(
                json.dumps(
                    {
                        "status": result["status"],
                        "models": len(result["models"]),
                        "schema": a.out,
                    }
                )
            )
            return 0 if result["models"] else 2
        if a.command == "infer":
            result = infer([read(f) for f in a.files])
            save(a.out, (json.dumps(result, indent=2) + "\n").encode())
            print(
                json.dumps(
                    {
                        "status": result["status"],
                        "models": len(result["models"]),
                        "schema": a.out,
                    }
                )
            )
            return 0 if result["models"] else 2
        if a.command == "serve":
            from .server import serve

            serve(a.port)
            return 0
        model = choose(a.schema, a.model)
        if a.command == "export-relocation":
            save(a.out, export_relocation(model).encode())
            print(json.dumps({"editor": a.out}))
            return 0
        if a.command in ("inspect-relocation", "relocate"):
            data = read(a.file)
            if a.command == "inspect-relocation":
                print(
                    json.dumps(
                        inspect_relocation(data, model, a.start, a.end), indent=2
                    )
                )
                return 0
            output, receipt = resize_relocation(
                data, model, a.start, a.end, read(a.payload)
            )
            save(a.out, output)
            print(json.dumps({"output": a.out, **receipt}))
            return 0
        if a.command == "export":
            save(a.out, export_standalone(model).encode())
            print(json.dumps({"codec": a.out}))
            return 0
        data = read(a.file)
        doc = parse(data, model)
        if a.command == "inspect":
            print(json.dumps(doc, indent=2))
            return 0
        path = [int(x) for x in a.path.split("/")]
        payload = read(a.payload).hex() if a.payload else a.hex
        output = rebuild(doc, model, [{"path": path, "payloadHex": payload}])
        save(a.out, output)
        print(
            json.dumps(
                {
                    "originalBytes": len(data),
                    "editedBytes": len(output),
                    "output": a.out,
                }
            )
        )
        return 0
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        OverflowError,
        RecursionError,
    ) as e:
        print(
            json.dumps({"error": str(e), "code": getattr(e, "code", "invalid-input")}),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
