# MIT License
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

# MIT License. Copyright (c) 2026 Nazeeh Abdul-Hadi.
"""Learn guarded single-span byte counts and absolute suffix relocation.

No format database or parser is used. The marked span is supplied by a human.
Literal bytes outside learned fields and the span define a document family,
not a complete format specification. IDs are integrity identities, not signatures.
"""

import hashlib
import itertools
import json

MAX_FILE = 1024 * 1024
MAX_CORPUS_BYTES = 4 * MAX_FILE
MAX_MODELS = 128
MAX_FIELDS = 32
MAX_CANDIDATES = 128
MAX_VARYING = 128
MAX_SEARCH = 10000
MAX_MODEL_BYTES = 3 * MAX_FILE
MAX_RESULT_BYTES = 8 * MAX_FILE


class RelocationError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code

    def as_dict(self):
        return {"code": self.code, "message": str(self), "context": {}}


def _fail(code, message):
    raise RelocationError(code, message)


def _int(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        _fail("invalid-data", label + " is outside its allowed range")
    return value


def _blob(data, empty=False):
    if type(data) is not bytes or len(data) > MAX_FILE or (not empty and not data):
        _fail("input-bound", "Input must be bounded bytes, at most 1 MiB")
    return data


def _hex(value, limit, label):
    if type(value) is not str or len(value) > 2 * limit or len(value) % 2:
        _fail("invalid-model", label + " must be bounded hexadecimal")
    if any(c not in "0123456789abcdef" for c in value):
        _fail("invalid-model", label + " must use canonical lowercase hexadecimal")
    return bytes.fromhex(value)


def _canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _id(model):
    return hashlib.sha256(
        _canon({k: v for k, v in model.items() if k != "id"}).encode()
    ).hexdigest()


def _align(end, alignment):
    return (end + alignment - 1) // alignment * alignment


def _unique(data, anchor):
    first = data.find(anchor)
    return first >= 0 and data.find(anchor, first + 1) < 0


def _span(data, start, end, alignment):
    _int(start, 0, len(data), "Span start")
    _int(end, start, len(data), "Span end")
    padded = _align(end, alignment)
    if padded > len(data) or any(data[end:padded]):
        _fail("padding", "Marked span has missing or nonzero alignment padding")
    return padded


def _site(field, padded):
    return field["offset"] if field["region"] == "prefix" else padded + field["offset"]


def _masked(data, fields, region):
    masked = bytearray(data)
    for f in fields:
        if f["region"] == region:
            masked[f["offset"] : f["offset"] + f["width"]] = bytes(f["width"])
    return bytes(masked)


def validate_relocation(model):
    """Validate exact supported laws; rehashing unsupported properties grants no support."""
    if type(model) is not dict:
        _fail("invalid-model", "Relocation model must be an object")
    try:
        if len(_canon(model).encode()) > MAX_MODEL_BYTES:
            _fail("invalid-model", "Relocation model exceeds 3 MiB")
    except (TypeError, ValueError, RecursionError):
        _fail("invalid-model", "Relocation model must be bounded JSON")
    keys = {
        "version",
        "kind",
        "id",
        "alignment",
        "start",
        "referenceLength",
        "prefixHex",
        "suffixHex",
        "fields",
        "evidence",
    }
    if (
        set(model) != keys
        or model["kind"] != "relocation"
        or type(model["version"]) is not int
        or model["version"] != 1
    ):
        _fail("invalid-model", "Unsupported relocation properties or version")
    if type(model["alignment"]) is not int or model["alignment"] not in (1, 2, 4, 8):
        _fail("invalid-model", "Unsupported alignment")
    start = _int(model["start"], 0, MAX_FILE, "Model span start")
    length = _int(model["referenceLength"], 0, MAX_FILE, "Reference length")
    padded = _align(start + length, model["alignment"])
    prefix = _hex(model["prefixHex"], MAX_FILE, "Prefix")
    suffix = _hex(model["suffixHex"], MAX_FILE, "Suffix")
    if (
        len(prefix) != start
        or padded + len(suffix) > MAX_FILE
        or padded + len(suffix) < 1
    ):
        _fail("invalid-model", "Invalid reference envelope bounds")
    fields = model["fields"]
    if type(fields) is not list or not 1 <= len(fields) <= MAX_FIELDS:
        _fail("invalid-model", "Model requires 1–32 bounded fields")
    used = {"prefix": set(), "suffix": set()}
    for f in fields:
        if type(f) is not dict or set(f) != {
            "kind",
            "region",
            "offset",
            "width",
            "endian",
            "targetOffset",
            "anchorHex",
        }:
            _fail("invalid-model", "Invalid relocation field properties")
        if (
            type(f["kind"]) is not str
            or type(f["region"]) is not str
            or f["kind"] not in ("spanLength", "absoluteSuffixPointer")
            or f["region"] not in used
        ):
            _fail("invalid-model", "Unsupported relocation field interpretation")
        if (
            type(f["width"]) is not int
            or f["width"] not in (2, 4)
            or f["endian"] not in ("little", "big")
        ):
            _fail("invalid-model", "Unsupported integer width or endian")
        envelope = prefix if f["region"] == "prefix" else suffix
        offset = _int(f["offset"], 0, len(envelope), "Field offset")
        positions = set(range(offset, offset + f["width"]))
        if offset + f["width"] > len(envelope) or used[f["region"]] & positions:
            _fail("invalid-model", "Field storage overlaps another field or span")
        used[f["region"]].update(positions)
        observed = int.from_bytes(envelope[offset : offset + f["width"]], f["endian"])
        if f["kind"] == "spanLength":
            if (
                f["targetOffset"] is not None
                or f["anchorHex"] is not None
                or observed != length
            ):
                _fail("invalid-model", "Reference does not satisfy span length law")
        else:
            target = _int(f["targetOffset"], 0, len(suffix), "Suffix target")
            anchor = _hex(f["anchorHex"], 8, "Pointer anchor")
            if (
                len(anchor) != 8
                or target + 8 > len(suffix)
                or suffix[target : target + 8] != anchor
                or not _unique(suffix, anchor)
            ):
                _fail(
                    "invalid-model",
                    "Pointer anchor must be a unique eight-byte suffix region",
                )
            if observed != padded + target:
                _fail("invalid-model", "Reference does not satisfy pointer law")
    if not any(f["kind"] == "spanLength" for f in fields):
        _fail("invalid-model", "An editable span needs an observed byte-count law")
    # Dynamic pointer storage may not overwrite a target anchor, including its own.
    for f in fields:
        if f["kind"] == "absoluteSuffixPointer" and used["suffix"] & set(
            range(f["targetOffset"], f["targetOffset"] + 8)
        ):
            _fail("invalid-model", "Pointer anchor overlaps dynamic integer storage")
    evidence = model["evidence"]
    if type(evidence) is not list or not 2 <= len(evidence) <= 8:
        _fail("invalid-model", "Model needs 2–8 observation receipts")
    total = 0
    for row in evidence:
        if type(row) is not dict or set(row) != {"sha256", "bytes", "start", "end"}:
            _fail("invalid-model", "Invalid observation receipt")
        digest = _hex(row["sha256"], 32, "Observation digest")
        if len(digest) != 32:
            _fail("invalid-model", "Observation digest must be SHA256")
        n = _int(row["bytes"], 1, MAX_FILE, "Observation size")
        a = _int(row["start"], 0, n, "Observation start")
        b = _int(row["end"], a, n, "Observation end")
        if a != start or _align(b, model["alignment"]) + len(suffix) != n:
            _fail(
                "invalid-model",
                "Observation receipt differs from fixed envelope topology",
            )
        total += n
    if total > MAX_CORPUS_BYTES or len({r["end"] - r["start"] for r in evidence}) < 2:
        _fail("invalid-model", "Observation lengths must vary within the corpus bound")
    if evidence[0]["end"] - evidence[0]["start"] != length:
        _fail(
            "invalid-model",
            "Reference length differs from its first observation receipt",
        )
    if type(model["id"]) is not str or model["id"] != _id(model):
        _fail("invalid-model", "Model identity does not match its properties")
    return model


def inspect_relocation(data, model, start, end):
    validate_relocation(model)
    _blob(data)
    padded = _span(data, start, end, model["alignment"])
    minimum = min(r["end"] - r["start"] for r in model["evidence"])
    if end - start < minimum:
        _fail(
            "minimum-length",
            "Marked length is below the smallest observed document-family length",
        )
    if start != model["start"]:
        _fail("family-guard", "Marked start differs from observed document family")
    prefix = bytes.fromhex(model["prefixHex"])
    suffix = bytes.fromhex(model["suffixHex"])
    if len(data) - padded != len(suffix):
        _fail("family-guard", "Suffix length differs from observed document family")
    for region, current, reference in (
        ("prefix", data[:start], prefix),
        ("suffix", data[padded:], suffix),
    ):
        if _masked(current, model["fields"], region) != _masked(
            reference, model["fields"], region
        ):
            _fail(
                "family-guard",
                "Untouched literal envelope differs from observed document family",
            )
    fields = []
    for f in model["fields"]:
        site = _site(f, padded)
        value = int.from_bytes(data[site : site + f["width"]], f["endian"])
        expected = (
            end - start if f["kind"] == "spanLength" else padded + f["targetOffset"]
        )
        if value != expected:
            _fail("field-law", "Fresh file violates a learned byte count or pointer")
        row = dict(f, site=site, value=value)
        if f["kind"] == "absoluteSuffixPointer":
            anchor = bytes.fromhex(f["anchorHex"])
            if data[value : value + 8] != anchor or not _unique(data[padded:], anchor):
                _fail("pointer-anchor", "Pointer anchor is missing or ambiguous")
            row["target"] = value
        fields.append(row)
    return {
        "kind": "relocation",
        "modelId": model["id"],
        "start": start,
        "end": end,
        "paddedEnd": padded,
        "payloadHex": data[start:end].hex(),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "fields": fields,
        "alignment": model["alignment"],
        "minimumLength": minimum,
        "scope": "Fixed document envelope; marked span semantics supplied explicitly",
    }


def resize_relocation(data, model, start, end, replacement):
    before = inspect_relocation(data, model, start, end)
    _blob(replacement, empty=True)
    if len(replacement) < before["minimumLength"]:
        _fail(
            "minimum-length",
            "Replacement is below the smallest observed document-family length; supply smaller observations first",
        )
    new_end = start + len(replacement)
    new_padded = _align(new_end, model["alignment"])
    total = new_padded + len(data) - before["paddedEnd"]
    if not 1 <= total <= MAX_FILE:
        _fail("output-bound", "Edited file exceeds the 1 MiB bound or is empty")
    result = bytearray(
        data[:start]
        + replacement
        + bytes(new_padded - new_end)
        + data[before["paddedEnd"] :]
    )
    changes = []
    for f in before["fields"]:
        new_site = _site(f, new_padded)
        value = (
            len(replacement)
            if f["kind"] == "spanLength"
            else new_padded + f["targetOffset"]
        )
        if value >= 1 << (8 * f["width"]):
            _fail(
                "integer-overflow", "Replacement exceeds selected integer field width"
            )
        result[new_site : new_site + f["width"]] = value.to_bytes(
            f["width"], f["endian"]
        )
        changes.append(
            {
                "kind": f["kind"],
                "site": f["site"],
                "newSite": new_site,
                "before": f["value"],
                "after": value,
            }
        )
    result = bytes(result)
    inspect_relocation(result, model, start, new_end)
    receipt = {
        "kind": "relocation",
        "modelId": model["id"],
        "originalBytes": len(data),
        "editedBytes": len(result),
        "sha256": hashlib.sha256(result).hexdigest(),
        "originalSha256": before["sha256"],
        "start": start,
        "end": new_end,
        "paddedEnd": new_padded,
        "changes": changes,
        "unchangedEnvelopeVerified": True,
    }
    return result, receipt


def infer_relocation(samples, spans):
    if type(samples) not in (list, tuple) or not 2 <= len(samples) <= 8:
        _fail("input-bound", "Supply 2–8 related files")
    if type(spans) not in (list, tuple) or len(spans) != len(samples):
        _fail("invalid-span", "Each observation requires a matching marked span")
    for data in samples:
        _blob(data)
    if sum(map(len, samples)) > MAX_CORPUS_BYTES:
        _fail("input-bound", "Corpus exceeds 4 MiB")
    checked = []
    for data, span in zip(samples, spans):
        if type(span) not in (list, tuple) or len(span) != 2:
            _fail("invalid-span", "Span must be [start,end]")
        a = _int(span[0], 0, len(data), "Marked start")
        b = _int(span[1], a, len(data), "Marked end")
        checked.append((a, b))
    evidence = [
        {"sha256": hashlib.sha256(d).hexdigest(), "bytes": len(d), "start": a, "end": b}
        for d, (a, b) in zip(samples, checked)
    ]
    models, truncated, visits, result_bytes = [], False, 0, 0
    limits = set()
    if len({a for a, _ in checked}) != 1 or len({b - a for a, b in checked}) < 2:
        return {
            "kind": "relocation",
            "models": [],
            "status": "unsupported",
            "truncated": False,
            "experiments": [
                "Keep the same fixed envelope start and vary the marked field's byte length"
            ],
            "evidence": evidence,
        }
    start = checked[0][0]
    lengths = [b - a for a, b in checked]
    for alignment in (1, 2, 4, 8):
        try:
            padded = [_span(d, a, b, alignment) for d, (a, b) in zip(samples, checked)]
        except RelocationError:
            continue
        envelopes = {
            "prefix": [d[:start] for d in samples],
            "suffix": [d[p:] for d, p in zip(samples, padded)],
        }
        if len({len(d) for d in envelopes["suffix"]}) != 1:
            continue
        varying = set()
        for region, buffers in envelopes.items():
            for i, column in enumerate(zip(*buffers)):
                if column.count(column[0]) != len(column):
                    varying.add((region, i))
                    if len(varying) > MAX_VARYING:
                        break
            if len(varying) > MAX_VARYING:
                break
        if not varying:
            continue
        if len(varying) > MAX_VARYING:
            truncated = True
            limits.add("varyingBytes")
            continue
        candidates = []
        for region, buffers in envelopes.items():
            positions = sorted(
                {
                    max(0, i - bias)
                    for reg, i in varying
                    if reg == region
                    for bias in range(4)
                }
            )
            for pos, width, endian in itertools.product(
                positions, (2, 4), ("little", "big")
            ):
                if pos + width > len(buffers[0]):
                    continue
                values = [int.from_bytes(d[pos : pos + width], endian) for d in buffers]
                if len(set(values)) < 2:
                    continue
                field = {
                    "kind": None,
                    "region": region,
                    "offset": pos,
                    "width": width,
                    "endian": endian,
                    "targetOffset": None,
                    "anchorHex": None,
                }
                if values == lengths:
                    field["kind"] = "spanLength"
                else:
                    offsets = {v - p for v, p in zip(values, padded)}
                    if len(offsets) != 1:
                        continue
                    target = next(iter(offsets))
                    suffixes = envelopes["suffix"]
                    if target < 0 or target + 8 > len(suffixes[0]):
                        continue
                    anchors = [d[target : target + 8] for d in suffixes]
                    if len(set(anchors)) != 1 or any(
                        not _unique(d, anchors[0]) for d in suffixes
                    ):
                        continue
                    field.update(
                        kind="absoluteSuffixPointer",
                        targetOffset=target,
                        anchorHex=anchors[0].hex(),
                    )
                covered = {(region, i) for i in range(pos, pos + width)}
                candidates.append((field, covered))
        if len(candidates) > MAX_CANDIDATES:
            truncated = True
            limits.add("candidateFields")
            continue
        by_byte = {
            byte: [i for i, (_, covered) in enumerate(candidates) if byte in covered]
            for byte in varying
        }
        if any(not choices for choices in by_byte.values()):
            continue

        def search(chosen, used):
            nonlocal truncated, visits, result_bytes
            visits += 1
            if visits > MAX_SEARCH or len(models) >= MAX_MODELS:
                truncated = True
                limits.add("searchVisits" if visits > MAX_SEARCH else "models")
                return
            remaining = varying - used
            if remaining:
                byte = min(remaining, key=lambda item: (len(by_byte[item]), item))
                for index in by_byte[byte]:
                    field, covered = candidates[index]
                    if used & covered or len(chosen) >= MAX_FIELDS:
                        continue
                    search(chosen + [field], used | covered)
                    if (
                        visits > MAX_SEARCH
                        or len(models) >= MAX_MODELS
                        or "resultBytes" in limits
                    ):
                        break
                return
            fields = sorted(
                chosen,
                key=lambda f: (f["region"], f["offset"], f["width"], f["endian"]),
            )
            if not any(f["kind"] == "spanLength" for f in fields):
                return
            model = {
                "version": 1,
                "kind": "relocation",
                "alignment": alignment,
                "start": start,
                "referenceLength": lengths[0],
                "prefixHex": envelopes["prefix"][0].hex(),
                "suffixHex": envelopes["suffix"][0].hex(),
                "fields": fields,
                "evidence": evidence,
            }
            model["id"] = _id(model)
            try:
                validate_relocation(model)
                for d, (a, b) in zip(samples, checked):
                    inspect_relocation(d, model, a, b)
            except RelocationError:
                return
            if all(m["id"] != model["id"] for m in models):
                n = len(_canon(model).encode())
                if result_bytes + n > MAX_RESULT_BYTES:
                    truncated = True
                    limits.add("resultBytes")
                    return
                result_bytes += n
                models.append(model)

        search([], set())
        if visits > MAX_SEARCH or len(models) >= MAX_MODELS or "resultBytes" in limits:
            break
    status = (
        "ambiguous"
        if truncated or len(models) > 1
        else "unique"
        if len(models) == 1
        else "unsupported"
    )
    experiments = []
    if status != "unique":
        experiments = [
            "Record odd and even marked lengths crossing different alignment boundaries",
            "Include a marked length or target offset above 65535 to distinguish narrow integer encodings",
            "Do not resize when changing envelope bytes remain unexplained or search is truncated",
        ]
    return {
        "kind": "relocation",
        "models": models,
        "status": status,
        "truncated": truncated,
        "experiments": experiments,
        "evidence": evidence,
        "searchVisits": visits,
        "modelBytes": result_bytes,
        "limits": sorted(limits),
        "scope": "One explicitly marked span; absolute suffix pointers; literal untouched envelope guarded",
    }


MODEL=json.loads('{"alignment":2,"evidence":[{"bytes":982,"end":167,"sha256":"7b44e6b9f72de4b3da39599061695020047481830df312489af63407fd430c8e","start":146},{"bytes":982,"end":168,"sha256":"f7f4d8e98e88473c7a1f351ecb4f39ef20c5e8d683e4510991d352f15b4da679","start":146},{"bytes":984,"end":169,"sha256":"47fbe85b99752438e9ac1870e493da813a27caa8c35ae811a2a307861e2f27e8","start":146},{"bytes":1000,"end":185,"sha256":"b9aab8bfaa9b19d1cf97ac5feef77e0d6a3fd8ed8c7fc70aca99195417a466ed","start":146},{"bytes":1022,"end":207,"sha256":"77370251d7fe5071b6afc1275272479f2601a3d8a9ee72e4a9aaaca26b9cce78","start":146},{"bytes":70972,"end":70157,"sha256":"8d28c20b914bcc062384adde64c950b14d7dbd9a9c4f2018a14f3d5dbf9af6e8","start":146},{"bytes":966,"end":151,"sha256":"811726601baf404749a217e603c2b95979c4adc56577e7a0f9236778ba8649bb","start":146},{"bytes":1006,"end":192,"sha256":"e0ed3ac873f2ff7f2dcade23b9c0f68f42af040b236c11ae20128dacd5f91623","start":146}],"fields":[{"anchorHex":null,"endian":"little","kind":"spanLength","offset":74,"region":"prefix","targetOffset":null,"width":4},{"anchorHex":"dbb850636b1db9a8","endian":"little","kind":"absoluteSuffixPointer","offset":90,"region":"prefix","targetOffset":46,"width":4},{"anchorHex":"4f776e6564206f70","endian":"little","kind":"absoluteSuffixPointer","offset":138,"region":"prefix","targetOffset":0,"width":4}],"id":"dc5d2a639e714d012c7f72ad36c05f57e5b8b019cab554f53cf8bce7a531d232","kind":"relocation","prefixHex":"49492a00080000000b000001040001000000200000000101040001000000180000000201030001000000080000000301030001000000010000000601030001000000010000000e01020015000000920000001101040001000000d60000001601040001000000180000001701040001000000000300001c0103000100000001000000e8fd02002d000000a800000000000000","referenceLength":21,"start":146,"suffixHex":"4f776e6564206f7061717565206d65746164617461206d7573742072656d61696e20627974652065786163740000dbb850636b1db9a8fbfdbe9a3c61a16f6cfe4ceeb589ef845408238af5c2a4d6293fc27313863dba271d5a424f2eaae0a66b60a45b8cadcdb9ae0b70cf8005a3af73c4798192d431d39de6bf050d03359fa5d04fb4bb0140842ee927a6844e71935c9536afb298b3431d1fa47127a46f3c6a321b57d61462a7a25a069ad344c2ee03db2dae34ad433149b64b3985dfd6f4d4c9fc1d0bed4ee33778af8ad31a00be893d5aeb40283837581557dccfd309477a25c353f4cc278f36d646cf995a42c5c1a49f6e15683b763a924b0c969e50f808feaf08bc2cd151ebfc86d78b56141ccf109c4bc251a6093285a2887af5a976707a137aabe09a93343fbe343bef7e0e00ae9a2952febcd1ae14433f2736a509a30b03ec1f4af69f9484ad1f3b76f914231742d137062429b5abec44919798f2fd6a41f9d4970367545d41960d6be8643c35367358dc52af42c70ab1bd07efaed369b16e5c7eac24467bbd642ac6db863f46303e67df6166231b993c60d5b72b5fa41c1ef18b8f21b3c00ef48ad7cb5c7d50341f0327570ab1e3404064f482544e1fedc8e3a586ffdd3d6b592531ac78280fceeddc417cff878d034241cc52771de7f12adf2fe1f7a2a1a9b98b97c0db942b883a2af5a1dbe20f73540fa6401d4d236124ad4ae16a066f8a7c63eb1d0cd27d2fe18d6fff3cf4d387300188d5cde63c7b2d1affad137e989517284b777d30e29c84a91003d0825b83da3c885aaa3db91fc032d9b3ffa2dd16a8d23d8ed6e3429cd038127a79f0f4859eddb7bd4c08a00f17aad340c4af8bfaba3886afd7a731d0e7f0fbdf16ac7a62238e4fac5b041d4407e16f5afe50fea42c9722ef9ee2d26be46464efe191a543c5afa595bc8d6219737d9ca22727b05d7d260c2eddf15476d3c9530bbf192341bd313637fe80936cb67a99bd719c5eaff555c28b8668a92005ab6af35e2907d849c091a242b6bff31845247d75177804c02302d6909c1359bbc68f31c9cac1626756b10d6d7f3581dc5f92fe03b84225821df49d9baefe4ec89af243a190ab06b8c645076dbaf92fe6265f5aa55322ab0c570929","version":1}')

def main(argv=None):
    import argparse
    import sys
    from pathlib import Path
    parser = argparse.ArgumentParser(description="Standalone guarded marked-span relocation editor")
    commands = parser.add_subparsers(dest="command", required=True)
    view = commands.add_parser("inspect")
    edit = commands.add_parser("rebuild")
    for command in (view, edit):
        command.add_argument("file")
        command.add_argument("--start", type=int, required=True)
        command.add_argument("--end", type=int, required=True)
    edit.add_argument("payload")
    edit.add_argument("output")
    args = parser.parse_args(argv)
    try:
        with Path(args.file).open("rb") as source:
            data = source.read(MAX_FILE + 1)
        if args.command == "inspect":
            print(json.dumps(inspect_relocation(data, MODEL, args.start, args.end), indent=2))
            return 0
        with Path(args.payload).open("rb") as payload:
            replacement = payload.read(MAX_FILE + 1)
        result, receipt = resize_relocation(data, MODEL, args.start, args.end, replacement)
        with Path(args.output).open("xb") as target:
            target.write(result)
        print(json.dumps(receipt))
        return 0
    except RelocationError as error:
        print(json.dumps({"error": error.as_dict()}), file=sys.stderr)
        return 2
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        print(json.dumps({"error": {"code": "io-or-data", "message": str(error)}}), file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
