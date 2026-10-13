import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from reclay.relocation import (
    infer_relocation,
    inspect_relocation,
    resize_relocation,
    validate_relocation,
    export_relocation,
    RelocationError,
)

ROOT = Path(__file__).resolve().parents[1]
EX = ROOT / "reclay/examples/relocation"
MANIFEST = json.loads((EX / "manifest.json").read_text())


def synthetic(length, suffix_field=False, endian="little"):
    start = 16
    padded = (start + length + 1) // 2 * 2
    prefix = (
        b"OWN!"
        + length.to_bytes(4, endian)
        + (padded + 16).to_bytes(4, endian)
        + b"LOCK"
    )
    suffix = (
        ((padded + 16).to_bytes(4, endian) if suffix_field else b"KEEP")
        + b"reserved1234"
        + b"unique anchor bytes and opaque tail"
    )
    return prefix + b"x" * length + bytes(padded - start - length) + suffix


class RelocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples = [(EX / row["name"]).read_bytes() for row in MANIFEST["training"]]
        cls.spans = [(row["start"], row["end"]) for row in MANIFEST["training"]]
        cls.learned = infer_relocation(cls.samples, cls.spans)
        cls.model = cls.learned["models"][0]
        cls.working = (EX / MANIFEST["working"]["name"]).read_bytes()
        cls.start, cls.end = MANIFEST["working"]["start"], MANIFEST["working"]["end"]

    def test_real_producer_fixture_hashes_and_marked_spans(self):
        self.assertEqual(MANIFEST["producer"]["tool"], "Pillow")
        for row in MANIFEST["training"] + [MANIFEST["working"]]:
            data = (EX / row["name"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), row["sha256"])
            self.assertEqual(len(data), row["bytes"])
            self.assertTrue(data[row["start"] : row["end"]].endswith(b"\0"))
        for row in MANIFEST["heldout"]:
            self.assertEqual(
                hashlib.sha256((EX / row["payload"]).read_bytes()).hexdigest(),
                row["payloadSha256"],
            )
            self.assertEqual(
                hashlib.sha256((EX / row["oracle"]).read_bytes()).hexdigest(),
                row["oracleSha256"],
            )

    def test_boundary_corpus_has_one_complete_model(self):
        self.assertEqual(self.learned["status"], "unique")
        self.assertFalse(self.learned["truncated"])
        self.assertEqual(len(self.learned["models"]), 1)
        self.assertEqual(self.model["alignment"], 2)
        self.assertEqual(len(self.model["fields"]), 3)
        self.assertEqual(self.model["id"], MANIFEST["validation"]["trainingModelId"])
        for data, (a, b) in zip(self.samples, self.spans):
            viewed = inspect_relocation(data, self.model, a, b)
            result, receipt = resize_relocation(data, self.model, a, b, data[a:b])
            self.assertEqual(result, data)
            self.assertEqual(viewed["payloadHex"], data[a:b].hex())
            self.assertTrue(receipt["unchangedEnvelopeVerified"])

    def test_short_observations_retain_width_endian_ambiguity(self):
        result = infer_relocation(self.samples[:3], self.spans[:3])
        self.assertEqual(result["status"], "ambiguous")
        self.assertGreater(len(result["models"]), 1)
        self.assertTrue(result["experiments"])
        self.assertFalse(result["truncated"])
        self.assertGreater(
            len({f["width"] for m in result["models"] for f in m["fields"]}), 1
        )

    def test_fresh_short_long_parity_and_wide_edits_match_independent_writer(self):
        for row in MANIFEST["heldout"]:
            payload = (EX / row["payload"]).read_bytes()
            expected = (EX / row["oracle"]).read_bytes()
            result, receipt = resize_relocation(
                self.working, self.model, self.start, self.end, payload
            )
            self.assertEqual(result, expected)
            self.assertEqual(receipt["originalBytes"], len(self.working))
            self.assertEqual(receipt["editedBytes"], len(expected))
            self.assertEqual(hashlib.sha256(result).hexdigest(), receipt["sha256"])
        self.assertEqual(
            hashlib.sha256(self.working).hexdigest(), MANIFEST["working"]["sha256"]
        )

    def test_growth_shrink_zero_and_suffix_pointer_storage(self):
        for endian in ("little", "big"):
            sizes = (0, 1, 2, 70001)
            samples = [synthetic(n, True, endian) for n in sizes]
            learned = infer_relocation(samples, [(16, 16 + n) for n in sizes])
            self.assertEqual(learned["status"], "unique", endian)
            model = learned["models"][0]
            self.assertTrue(any(f["region"] == "suffix" for f in model["fields"]))
            data = synthetic(35, True, endian)
            for n in (0, 1, 14, 70, 80002):
                result, receipt = resize_relocation(data, model, 16, 51, b"x" * n)
                self.assertEqual(result, synthetic(n, True, endian))
                self.assertTrue(
                    any(c["site"] != c["newSite"] for c in receipt["changes"])
                )

    def test_unexplained_byte_and_outside_document_family_are_rejected(self):
        broken = bytearray(self.samples[1])
        broken[-1] ^= 1
        inferred = infer_relocation([self.samples[0], bytes(broken)], self.spans[:2])
        self.assertEqual(inferred["status"], "unsupported")
        self.assertFalse(inferred["models"])
        for pos in (0, self.start - 1, len(self.working) - 1):
            modified = bytearray(self.working)
            modified[pos] ^= 1
            with self.assertRaises(RelocationError):
                inspect_relocation(bytes(modified), self.model, self.start, self.end)
        with self.assertRaises(RelocationError):
            inspect_relocation(self.working, self.model, self.start + 1, self.end)

    def test_bad_count_pointer_and_padding_refuse_edit(self):
        for f in self.model["fields"]:
            modified = bytearray(self.working)
            modified[f["offset"]] ^= 1
            with self.assertRaises(RelocationError):
                resize_relocation(
                    bytes(modified), self.model, self.start, self.end, b"new"
                )
        data = bytearray(self.samples[0])
        a, b = self.spans[0]
        self.assertNotEqual(b % 2, 0)
        data[b] = 1
        with self.assertRaises(RelocationError):
            inspect_relocation(bytes(data), self.model, a, b)

    def test_unobserved_short_storage_switch_is_refused(self):
        view = inspect_relocation(self.working, self.model, self.start, self.end)
        self.assertEqual(view["minimumLength"], 5)
        for replacement in (b"", b"\0", b"a\0", b"abc\0"):
            with self.assertRaises(RelocationError) as error:
                resize_relocation(
                    self.working, self.model, self.start, self.end, replacement
                )
            self.assertEqual(error.exception.code, "minimum-length")
        # The actual five-byte observation defines the floor and exactly round trips.
        index = next(i for i, (a, b) in enumerate(self.spans) if b - a == 5)
        data, (a, b) = self.samples[index], self.spans[index]
        result, _ = resize_relocation(data, self.model, a, b, data[a:b])
        self.assertEqual(result, data)

    def test_rehashed_unsupported_model_is_still_rejected(self):
        import reclay.relocation as module

        for mutation in ("width", "kind", "overlap", "anchor", "reference"):
            model = copy.deepcopy(self.model)
            if mutation == "width":
                model["fields"][0]["width"] = 8
            elif mutation == "kind":
                model["fields"][1]["kind"] = "signedRelativePointer"
            elif mutation == "overlap":
                model["fields"].append(copy.deepcopy(model["fields"][0]))
            elif mutation == "anchor":
                model["fields"][1]["anchorHex"] = "00" * 8
            else:
                model["referenceLength"] += 1
            model["id"] = module._id(model)
            with self.assertRaises(RelocationError):
                validate_relocation(model)
        model = copy.deepcopy(self.model)
        model["id"] = "0" * 64
        with self.assertRaises(RelocationError):
            validate_relocation(model)

    def test_overlapping_anchor_matches_are_ambiguous(self):
        import reclay.relocation as module

        model = copy.deepcopy(self.model)
        pointer = next(
            f for f in model["fields"] if f["kind"] == "absoluteSuffixPointer"
        )
        target = pointer["targetOffset"]
        suffix = bytearray.fromhex(model["suffixHex"])
        suffix[target : target + 10] = b"ababababab"
        pointer["anchorHex"] = b"abababab".hex()
        model["suffixHex"] = suffix.hex()
        model["id"] = module._id(model)
        with self.assertRaises(RelocationError):
            validate_relocation(model)
        sizes = (8, 9, 10, 70001)
        samples = []
        for n in sizes:
            data = bytearray(synthetic(n, True))
            padded = (16 + n + 1) // 2 * 2
            data[padded + 16 : padded + 26] = b"ababababab"
            samples.append(bytes(data))
        result = infer_relocation(samples, [(16, 16 + n) for n in sizes])
        self.assertEqual(result["status"], "unsupported")
        self.assertFalse(result["models"])

    def test_small_selected_width_overflow_and_input_bounds(self):
        learned = infer_relocation(self.samples[:3], self.spans[:3])
        models = [
            m
            for m in learned["models"]
            if all(f["width"] == 2 and f["endian"] == "little" for f in m["fields"])
        ]
        self.assertTrue(models)
        with self.assertRaises(RelocationError):
            resize_relocation(self.samples[0], models[0], *self.spans[0], b"x" * 70000)
        for samples, spans in (
            ([b"a"], [(0, 1)]),
            ([b"x" * (1048576 + 1), b"a"], [(0, 1), (0, 1)]),
            ([b"a", b"b"], [(True, 1), (0, 1)]),
            ([b"a", b"b"], [(0, 2), (0, 1)]),
        ):
            with self.assertRaises(RelocationError):
                infer_relocation(samples, spans)
        with self.assertRaises(RelocationError):
            resize_relocation(
                self.working, self.model, self.start, self.end, b"x" * 1048576
            )

    def test_result_byte_and_search_limits_cannot_claim_unique(self):
        with patch("reclay.relocation.MAX_RESULT_BYTES", 1):
            result = infer_relocation(self.samples, self.spans)
            self.assertTrue(result["truncated"])
            self.assertNotEqual(result["status"], "unique")
            self.assertIn("resultBytes", result["limits"])
        with patch("reclay.relocation.MAX_SEARCH", 1):
            result = infer_relocation(self.samples, self.spans)
            self.assertTrue(result["truncated"])
            self.assertNotEqual(result["status"], "unique")

    def test_standalone_actual_isolated_process_and_no_clobber(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codec = root / "codec.py"
            codec.write_text(export_relocation(self.model))
            original = root / "file.bin"
            original.write_bytes(self.working)
            row = MANIFEST["heldout"][2]
            payload = root / "replacement.bin"
            payload.write_bytes((EX / row["payload"]).read_bytes())
            inspected = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    str(codec),
                    "inspect",
                    str(original),
                    "--start",
                    str(self.start),
                    "--end",
                    str(self.end),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(inspected.returncode, 0, inspected.stderr)
            self.assertEqual(json.loads(inspected.stdout)["modelId"], self.model["id"])
            output = root / "edited.bin"
            command = [
                sys.executable,
                "-I",
                str(codec),
                "rebuild",
                str(original),
                str(payload),
                str(output),
                "--start",
                str(self.start),
                "--end",
                str(self.end),
            ]
            rebuilt = subprocess.run(
                command, capture_output=True, text=True, timeout=10
            )
            self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
            self.assertEqual(output.read_bytes(), (EX / row["oracle"]).read_bytes())
            denied = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertEqual(denied.returncode, 2)
            self.assertEqual(original.read_bytes(), self.working)
            self.assertEqual(output.read_bytes(), (EX / row["oracle"]).read_bytes())


if __name__ == "__main__":
    unittest.main()
