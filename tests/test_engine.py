import unittest, pathlib, json, hashlib, tempfile, subprocess, sys, zlib, shutil, wave, io, copy
from reclay.engine import infer, parse, rebuild, ReclayError
from reclay.standalone import export_standalone

ROOT = pathlib.Path(__file__).resolve().parents[1]
EX = ROOT / "reclay/examples"
MANIFEST = json.loads((EX / "manifest.json").read_text())


def walk(chunks, path=()):
    for i, c in enumerate(chunks):
        yield path + (i,), c
        if c.get("children"):
            yield from walk(c["children"], path + (i,))


class CodecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.models = {}
        cls.results = {}
        for family in ("wav", "aiff", "png"):
            result = infer(
                [(EX / x["name"]).read_bytes() for x in MANIFEST["training"][family]]
            )
            cls.results[family] = result
            # Freeze from training only: full resize rules explain all changing prefix fields.
            # Other suffix candidates remain explicitly ambiguous in the public result.
            safe = [m for m in result["models"] if m["prefix"]["growthSafe"]]
            if len(safe) == 1:
                cls.models[family] = safe[0]

    def model(self, family):
        safe = [m for m in self.results[family]["models"] if m["prefix"]["growthSafe"]]
        self.assertEqual(
            len(safe),
            1,
            "Freeze the sole model whose changing prefix fields are explained, from training only",
        )
        return self.models[family]

    def test_corpus_hashes_are_real(self):
        for group in ("training", "heldOut"):
            for rows in MANIFEST[group].values():
                for row in rows:
                    data = (EX / row["name"]).read_bytes()
                    self.assertEqual(hashlib.sha256(data).hexdigest(), row["sha256"])
                    self.assertEqual(len(data), row["size"])

    def test_roundtrip_training_and_unseen_files(self):
        for family in ("wav", "aiff", "png"):
            model = self.model(family)
            for row in MANIFEST["training"][family] + MANIFEST["heldOut"][family]:
                data = (EX / row["name"]).read_bytes()
                doc = parse(data, model)
                self.assertEqual(rebuild(doc, model), data)

    def title_edit(self, family):
        model = self.model(family)
        original = (EX / MANIFEST["training"][family][0]["name"]).read_bytes()
        oracle = (EX / MANIFEST["heldOut"][family][0]["name"]).read_bytes()
        doc = parse(original, model)
        nodes = [
            (p, c)
            for p, c in walk(doc["chunks"])
            if bytes.fromhex(c["payloadHex"]).rstrip(b"\0") == b"river"
        ]
        self.assertEqual(
            len(nodes),
            1,
            "Metadata must be discovered as an editable block, not an offset template",
        )
        path, chunk = nodes[0]
        old = bytes.fromhex(chunk["payloadHex"])
        title = MANIFEST["heldOut"][family][0]["title"].encode()
        new = title + (b"\0" if old.endswith(b"\0") else b"")
        output = rebuild(doc, model, [{"path": list(path), "payloadHex": new.hex()}])
        self.assertNotEqual(output, original)
        self.assertEqual(output, oracle)
        self.assertEqual(rebuild(parse(original, model), model), original)
        return original, output

    def test_wav_unseen_title_matches_independent_ffmpeg(self):
        before, after = self.title_edit("wav")
        with wave.open(io.BytesIO(before)) as a, wave.open(io.BytesIO(after)) as b:
            self.assertEqual(a.getparams(), b.getparams())
            self.assertEqual(a.readframes(a.getnframes()), b.readframes(b.getnframes()))

    def test_aiff_unseen_title_matches_independent_ffmpeg(self):
        self.title_edit("aiff")

    def test_png_recompressed_payload_repairs_crc_and_preserves_pixels(self):
        model = self.model("png")
        before = (EX / MANIFEST["heldOut"]["png"][0]["name"]).read_bytes()
        doc = parse(before, model)
        nodes = [
            (p, c)
            for p, c in walk(doc["chunks"])
            if bytes.fromhex(c["tagHex"]) == b"IDAT"
        ]
        self.assertEqual(len(nodes), 1)
        path, c = nodes[0]
        raw = zlib.decompress(bytes.fromhex(c["payloadHex"]))
        payload = zlib.compress(raw, 0)
        output = rebuild(
            doc, model, [{"path": list(path), "payloadHex": payload.hex()}]
        )
        self.assertNotEqual(output, before)
        parse(output, model)
        for other in doc["chunks"]:
            if bytes.fromhex(other["tagHex"]) != b"IDAT":
                self.assertIn(bytes.fromhex(other["payloadHex"]), output)
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.skipTest(
                "Independent PNG pixel decode requires FFmpeg; structural reconstruction already checked"
            )

        def pixels(data):
            return subprocess.run(
                [
                    ffmpeg,
                    "-v",
                    "error",
                    "-f",
                    "image2pipe",
                    "-i",
                    "pipe:0",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "rgb24",
                    "pipe:1",
                ],
                input=data,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                timeout=10,
            ).stdout

        self.assertEqual(pixels(output), pixels(before))
        self.assertEqual(len(pixels(output)), 23 * 17 * 3)

    def test_frozen_standalone_codec_executes_without_reclay(self):
        model = self.model("wav")
        data = (EX / "river.wav").read_bytes()
        source = export_standalone(model)
        with tempfile.TemporaryDirectory() as temp:
            p = pathlib.Path(temp)
            (p / "codec.py").write_text(source)
            (p / "input.bin").write_bytes(data)
            runner = "import sys,pathlib; sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent)); import codec; d=codec.parse(open('input.bin','rb').read(),codec.MODEL); out=codec.rebuild(d,codec.MODEL); assert out==open('input.bin','rb').read(); print('standalone exact round trip')"
            (p / "run.py").write_text(runner)
            r = subprocess.run(
                [sys.executable, "-I", str(p / "run.py")],
                cwd=p,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("standalone exact", r.stdout)

    def test_damage_is_rejected_not_a_successful_edit(self):
        model = self.model("png")
        data = bytearray((EX / "pattern-23x17.png").read_bytes())
        data[-5] ^= 1
        with self.assertRaises(ValueError):
            parse(bytes(data), model)

    def test_inputs_outside_family_and_limits_are_rejected(self):
        result = infer([b"hello world" * 10, b"no chunks here" * 10])
        self.assertEqual(result["status"], "unsupported")
        self.assertFalse(result["models"])
        with self.assertRaises(ValueError):
            infer([b"x" * (1_048_576 + 1), b"x"])
        model = self.model("wav")
        doc = parse((EX / "river.wav").read_bytes(), model)
        for edits in [
            [{"path": [9999], "payloadHex": "00"}],
            [{"path": [0], "payloadHex": "zz"}],
            [{"path": [-1], "payloadHex": "00"}],
        ]:
            with self.assertRaises(ValueError):
                rebuild(doc, model, edits)

    def test_even_length_observations_keep_ambiguity(self):
        # A structure manufactured solely to falsify unjustified alignment certainty.
        import struct

        samples = [
            b"".join(b"TAG1" + struct.pack("<I", n) + b"x" * n for _ in range(2))
            for n in (4, 8)
        ]
        result = infer(samples)
        self.assertEqual(result["status"], "ambiguous")
        self.assertGreater(len(result["models"]), 1)
        self.assertTrue(result["experiments"])


if __name__ == "__main__":
    unittest.main()
