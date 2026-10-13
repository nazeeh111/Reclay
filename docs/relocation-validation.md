# Marked-span relocation: evidence and limits

The learner accepts bytes and explicit `[start,end]` annotations for one corresponding variable-length span. It does not identify TIFF or supply a TIFF schema. A candidate model explains observed changing envelope bytes using nonoverlapping byte-count fields and absolute pointers into the suffix, plus observed zero padding. Competing integer widths, byte order, field boundaries and alignments remain separate models. The model's SHA256 identifies its exact properties; it does not authenticate the author or observation receipts.

## Independent producer and fresh edits

`reclay/examples/relocation/manifest.json` records actual Pillow 12.3.0 production, hashes, marked spans, working-file identity, payloads and independently produced oracles. Eight observations contain owned deterministic 32×24 grayscale pixels, invented ASCII title and a private metadata value. Odd/even title lengths distinguish alignment. A 70,972-byte observation crosses the 16-bit boundary. An independently generated five-byte title observation establishes the smallest accepted document-family length.

The full corpus yields one complete model: little-endian four-byte count and two absolute suffix pointers, with two-byte alignment. The short corpus retains competing models. The held-out working file has a new title, not one from training. Four replacements cover shorter, longer, opposite parity and an 80-KiB title. Rebuilt bytes equal each separate Pillow output exactly. Actual local FFmpeg 7.1 reads all eight observations, four oracles and three below-floor native controls to the original 768 grayscale bytes. Pillow reopens the intended title, exact pixels and unchanged private metadata. Producer and reader execution occurs in the regeneration script; shipped fixture tests compare committed independent producer bytes without requiring Pillow or FFmpeg.

This is genuine TIFF output containing synthetic owned content. It is not recovered user data, an abandoned application's specimen or a complete TIFF editor. Pillow's producer is credited rather than copied into the runtime. The learner and exported editor use the Python standard library.

## Guarded document family

Fresh files must retain the observed prefix position, suffix size and every literal envelope byte outside learned integer storage. Pixel changes and unrelated metadata changes therefore require new observations. Counts, pointer values, unique target anchors and zero padding are checked before edits. Integer fields stored in the suffix move with the suffix; synthetic little- and big-endian graph controls independently exercise this mechanism. Changed envelope bytes with no supporting law produce no model. Unsupported structure, overlapping storage, overlapping anchor occurrences, malformed or unsupported rehashed properties, wrong counts/pointers, absent padding and overflow are rejected.

A fresh replacement must be at least as long as the smallest observed marked span. This is evidence-derived, not a TIFF threshold hardcoded into the learner. A reviewer verified that native Pillow switches a one-to-four-byte title into its directory value slot: blindly writing those titles into an external span could preserve image pixels while changing the title incorrectly. Actual native one-, two- and four-byte controls are recorded, and the editor refuses those replacements for this five-byte-minimum family. A generic family with a zero-byte observation can still support zero-length edits; the synthetic regression proves growth, shrink and zero with suffix-stored pointers. This lower bound does not prove all other semantic constraints. An arbitrary replacement can be structurally consistent and still be rejected by its original application.

Input limits are 2–8 files, 1 MiB each and 4 MiB total. Search covers alignments 1/2/4/8, two-/four-byte integers, 128 changing envelope bytes, 128 candidate fields, 32 selected fields, 10,000 search visits and at most 128 models. Model objects are bounded to 3 MiB; all retained compact models together are bounded to 8 MiB. Any exhausted limit is exposed as `truncated`; it cannot yield `status='unique'`. Unknown graph edges, changing topologies, relative pointers, checksums, compressed/encrypted content, many-span edits and format semantics are outside this release.

## Portable editor and verification

`export_relocation(model)` emits source with the full MIT notice and ordinary standalone Python inspect/rebuild commands. It imports no Reclay runtime and performs no evaluation of specimen content. Actual `python -I` subprocess tests inspect a fresh file and reconstruct a parity-changing held-out oracle exactly. Reads are bounded; output creation is exclusive. A second write to the same destination fails and preserves original and edited bytes.

The 13 focused regression tests cover producer hashes, short-corpus ambiguity, wide-corpus uniqueness, all four withheld oracles, literal guard refusal, padding/count/pointer errors, unsupported rehashed models, overlapping anchors, selected-width overflow, minimum-length refusal, result/search truncation and isolated standalone execution. These finite observations and controls support the declared mechanism, not universal correctness, semantic recovery or security claims.

To regenerate without overwriting shipped files, use a new directory:

```sh
python scripts/generate_relocation_examples.py --out /tmp/reclay-new-relocation-corpus
```

The producer requires Pillow and an independent FFmpeg reader. `--native-only` explicitly records `independentFFmpeg:false` if that reader is unavailable; it never substitutes a claimed independent pass.
