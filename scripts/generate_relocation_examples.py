"""Generate owned TIFF content with Pillow, verify edited outputs independently.

Dependencies are producer-only. The shipped learner/editor uses the stdlib.
The target must be a new directory; existing artifacts are never overwritten.
"""

import argparse
import hashlib
import io
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "reclay/examples/relocation")
    parser.add_argument(
        "--native-only",
        action="store_true",
        help="Explicitly skip the independent FFmpeg reader",
    )
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error(
            "Output must be a new directory; existing bytes are never overwritten"
        )
    try:
        from PIL import Image, TiffImagePlugin, __version__ as pillow_version
    except ImportError:
        parser.error(
            "Producer needs Pillow, but runtime and fixture-based tests do not"
        )
    ffmpeg = shutil.which("ffmpeg")
    if not args.native_only and not ffmpeg:
        parser.error(
            "Independent decode requires FFmpeg; use --native-only to record its absence explicitly"
        )
    sys.path.insert(0, str(ROOT))
    from reclay.relocation import infer_relocation, resize_relocation, RelocationError

    pixels = random.Random(684).randbytes(32 * 24)
    private = "Owned opaque metadata must remain byte exact"

    def produce(title):
        tags = TiffImagePlugin.ImageFileDirectory_v2()
        tags[270], tags[65000] = title, private
        out = io.BytesIO()
        Image.frombytes("L", (32, 24), pixels).save(
            out, format="TIFF", compression="raw", tiffinfo=tags
        )
        return out.getvalue()

    def mark(data, title):
        value = title.encode("ascii") + b"\0"
        if data.count(value) != 1:
            raise ValueError("Annotation is not unique")
        start = data.index(value)
        return start, start + len(value)

    def row(name, data, **extra):
        return dict(
            name=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), **extra
        )

    args.out.mkdir(parents=True)

    def write(name, data):
        with (args.out / name).open("xb") as target:
            target.write(data)

    def verify(name, data, title):
        with Image.open(io.BytesIO(data)) as reopened:
            if (
                reopened.tobytes() != pixels
                or reopened.tag_v2[270] != title
                or reopened.tag_v2[65000] != private
            ):
                raise ValueError("Pillow reopen differs from intended content")
        if not args.native_only:
            result = subprocess.run(
                [
                    ffmpeg,
                    "-v",
                    "error",
                    "-i",
                    str(args.out / name),
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "gray",
                    "-",
                ],
                capture_output=True,
                check=True,
                timeout=15,
            )
            if result.stdout != pixels:
                raise ValueError("Independent FFmpeg pixels differ")

    sizes = (10, 11, 12, 28, 50, 70000, 110, 35)
    titles = ["Afterlife " + "x" * n for n in sizes]
    titles[6] = (
        "abcd"  # Observe the five-byte value; no TIFF threshold is fed to the learner.
    )
    samples, spans, training = [], [], []
    for i, title in enumerate(titles):
        data = produce(title)
        a, b = mark(data, title)
        name = f"observation-{i}.tif"
        write(name, data)
        verify(name, data, title)
        samples.append(data)
        spans.append((a, b))
        training.append(
            row(
                name,
                data,
                start=a,
                end=b,
                content="Owned synthetic grayscale; marked ASCII title including its NUL terminator",
            )
        )
    learned = infer_relocation(samples, spans)
    if learned["status"] != "unique" or learned["truncated"]:
        raise ValueError("Boundary corpus did not yield one complete hypothesis")
    fresh_title = "Afterlife fresh file metadata that was withheld"
    fresh = produce(fresh_title)
    a, b = mark(fresh, fresh_title)
    write("working.tif", fresh)
    verify("working.tif", fresh, fresh_title)
    working = row("working.tif", fresh, start=a, end=b, title=fresh_title)
    replacement_titles = [
        "Afterlife short",
        "Afterlife independent heldout replacement " + "different-" * 38 + "done",
        "Afterlife independent heldout replacement " + "different-" * 38 + "done!",
        "Afterlife " + "wide-" * 16000,
    ]
    heldout = []
    for i, title in enumerate(replacement_titles):
        payload, oracle = title.encode() + b"\0", produce(title)
        output, receipt = resize_relocation(fresh, learned["models"][0], a, b, payload)
        if output != oracle:
            raise ValueError("Inferred edit differs from independently produced output")
        payload_name, oracle_name = f"replacement-{i}.bin", f"oracle-{i}.tif"
        write(payload_name, payload)
        write(oracle_name, oracle)
        verify(oracle_name, oracle, title)
        heldout.append(
            {
                "payload": payload_name,
                "oracle": oracle_name,
                "payloadBytes": len(payload),
                "payloadSha256": hashlib.sha256(payload).hexdigest(),
                "oracleBytes": len(oracle),
                "oracleSha256": hashlib.sha256(oracle).hexdigest(),
                "byteExactIndependentProducer": True,
                "receipt": receipt,
            }
        )
    negative = []
    for title in ("", "a", "abc"):
        payload = title.encode() + b"\0"
        native = produce(title)
        name = f"below-floor-{len(payload)}.tif"
        write(name, native)
        verify(name, native, title)
        try:
            resize_relocation(fresh, learned["models"][0], a, b, payload)
        except RelocationError as error:
            if error.code != "minimum-length":
                raise
        else:
            raise ValueError("Unobserved smaller storage family was accepted")
        negative.append(
            row(
                name,
                native,
                payloadBytes=len(payload),
                rejection="minimum-length",
                independentProducerReopenedExact=True,
            )
        )
    short = infer_relocation(samples[:3], spans[:3])
    manifest = {
        "description": "Learn distant offsets and a byte count from marked title observations; resize a fresh TIFF while preserving exact pixels and unrelated metadata.",
        "producer": {
            "tool": "Pillow",
            "version": pillow_version,
            "format": "raw TIFF",
            "content": "Owned deterministic synthetic 32×24 grayscale pixels and invented ASCII metadata",
            "seed": 684,
            "width": 32,
            "height": 24,
            "pixelSha256": hashlib.sha256(pixels).hexdigest(),
            "trainingTitleSuffixLengths": list(sizes),
            "trainingTitleOverrides": {"6": "abcd"},
        },
        "training": training,
        "working": working,
        "heldout": heldout,
        "negativeControls": negative,
        "validation": {
            "trainingModelId": learned["models"][0]["id"],
            "trainingStatus": learned["status"],
            "shortStatus": short["status"],
            "shortModels": len(short["models"]),
            "independentFFmpeg": not args.native_only,
            "ffmpegVersion": subprocess.check_output(
                [ffmpeg, "-version"], text=True
            ).splitlines()[0]
            if not args.native_only
            else None,
            "pillowReopenTitlePixelsPrivateMetadataExact": True,
            "allHeldoutByteExact": True,
        },
        "scope": "One marked span; observed zero alignment; fixed document topology and exact guarded pixels/private metadata. Not complete TIFF support or lost-application recovery.",
    }
    write("manifest.json", (json.dumps(manifest, indent=2) + "\n").encode())
    observations = [
        {"file": r["name"], "start": r["start"], "end": r["end"]} for r in training
    ]
    write("observations.json", (json.dumps(observations, indent=2) + "\n").encode())
    print(
        json.dumps(
            {
                "out": str(args.out),
                "training": len(training),
                "heldout": len(heldout),
                "status": learned["status"],
                "independentFFmpeg": not args.native_only,
            }
        )
    )


if __name__ == "__main__":
    main()
