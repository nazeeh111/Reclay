import pathlib, subprocess, math, struct, json, hashlib, shutil

root = pathlib.Path(__file__).resolve().parents[1] / "reclay" / "examples"
root.mkdir(parents=True, exist_ok=True)
ffmpeg = shutil.which("ffmpeg")
if not ffmpeg:
    raise SystemExit("Install FFmpeg to regenerate the independent example files")
manifest = {
    "producer": subprocess.check_output(
        [ffmpeg, "-version"], text=True
    ).splitlines()[0],
    "training": {},
    "heldOut": {},
    "commands": [],
}
for group, title, count in [
    ("training", "river", 40003),
    ("training", "riverbend", 40107),
    ("training", "skyline-observation", 70319),
    ("heldOut", "an independently resized title across odd alignment", 40003),
]:
    name = title.replace(" ", "-")[:30]
    raw = root / (name + ".raw")
    raw.write_bytes(bytes((i * 19 + i // 17) % 256 for i in range(count)))
    for ext in ("wav", "aiff"):
        dest = root / f"{name}.{ext}"
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "u8",
            "-ar",
            str(
                8000
                if title
                in ("river", "an independently resized title across odd alignment")
                else 12000
                if title == "riverbend"
                else 16000
            ),
            "-ac",
            "1",
            "-i",
            str(raw),
            "-metadata",
            "title=" + title,
            "-c:a",
            "pcm_u8" if ext == "wav" else "pcm_s8",
            str(dest),
        ]
        subprocess.run(cmd, check=True)
        manifest["commands"].append({"args": cmd, "exit": 0})
        manifest[group].setdefault(ext, []).append(
            {
                "name": dest.name,
                "size": dest.stat().st_size,
                "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
                "title": title,
                "frames": count,
            }
        )
    raw.unlink()
for group, width, height in [
    ("training", 11, 7),
    ("training", 13, 9),
    ("training", 19, 11),
    ("heldOut", 23, 17),
]:
    dest = root / f"pattern-{width}x{height}.png"
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size={width}x{height}:rate=1",
        "-frames:v",
        "1",
        "-update",
        "1",
        str(dest),
    ]
    subprocess.run(cmd, check=True)
    manifest["commands"].append({"args": cmd, "exit": 0})
    manifest[group].setdefault("png", []).append(
        {
            "name": dest.name,
            "size": dest.stat().st_size,
            "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
            "width": width,
            "height": height,
        }
    )
for entry in manifest["commands"]:
    entry["args"] = [
        (
            "ffmpeg"
            if i == 0
            else str(pathlib.Path(a).relative_to(root.parent.parent))
            if a.startswith(str(root.parent.parent) + "/")
            else a
        )
        for i, a in enumerate(entry["args"])
    ]
(root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(
    {
        k: {f: len(rows) for f, rows in manifest[k].items()}
        for k in ["training", "heldOut"]
    }
)
