# Reclay

**Turn examples of a binary format into a standalone editor.**

Give Reclay related files. For pointer-bearing layouts, mark the same variable-sized span in each observation and learn its byte counts, zero padding and absolute suffix pointers. For tagged blocks, it searches for rules governing tagged blocks, their lengths, padding and checksums, then compiles the surviving model into a dependency-free Python codec. Inspect a fresh file, resize a block, and watch its enclosing sizes, later positions and checksums change. Keep opaque payloads and original padding intact when they are untouched.

The learner receives bytes only. It does not use extensions, format names, magic-signature databases, an LLM, a supplied specification or the generating application's source.

[**Try Reclay in your browser →**](https://nazeeh111.github.io/Reclay/relocation.html)

![Actual browser Python engine resizing a title and rewriting two distant addresses](docs/browser-lab.jpg)

The [local pointer studio](docs/relocation-studio.jpg) and [nested block studio](docs/studio.png) remain available.

## Run it in your browser

[Open the browser laboratory](https://nazeeh111.github.io/Reclay/) or [go straight to pointer relocation](https://nazeeh111.github.io/Reclay/relocation.html). Both studios run the same shipped Python engine locally inside a Web Worker, using [Pyodide](https://pyodide.org/). No installation, model API, account or computation server is required. First computation downloads the pinned Python runtime from jsDelivr; this is not an offline-first guarantee. Your files and models stay in the browser.

The worker checks its Python source against the shipped manifest before loading it. Cancel stops the worker; the next operation creates a fresh one. Download edited files and ordinary standalone Python editors, then verify edits with an independent reader. The original local studio remains available below. [Browser architecture and limits](docs/browser-lab.md) describe hosting, source checks and cancellation.

For your own static deployment, build into a new directory using `python scripts/build_browser_lab.py --out /tmp/reclay-browser-new`, then serve it over localhost HTTP or HTTPS. The release includes the complete source-bearing browser lab ZIP. Opening file:// directly is unsupported.

## Keep internal pointers connected

Open **Learn internal pointers** in the studio. Load the TIFF experiment, learn its relationships, and inspect the withheld file. A larger description moves private metadata and image pixels. The learned editor rewrites the description's byte count and both addresses, preserving the remaining bytes. Preview shows the actual field values before and after; download a separate file or export a guarded standalone Python editor.

The examples are real Pillow-generated TIFF files containing original synthetic pixels and metadata. The inference engine receives file bytes and explicitly supplied matching spans; it does not receive TIFF tags or the writer's specification. Odd/even lengths resolve zero padding. A 70 KiB observation distinguishes integer encodings that fit shorter examples. New shorter, longer and 80 KiB replacements are checked against independently produced Pillow files and an independent FFmpeg decoder. [Evidence and exact scope](docs/relocation-validation.md) describe what was replicated.

This model covers a **document family**: one marked span and an unchanged envelope, including literal pixels and private metadata. It deliberately rejects a different image, changed directory topology or unexplained changing bytes. Edits below the shortest observed span refuse rather than cross an unseen storage transition. Unchanged dependencies can remain unobserved. A selected model is an assumption within the searched family, and application acceptance still needs its original reader. Relative pointers, cycles, compressed-content interpretation and automatic field semantics remain outside this version.

```sh
reclay infer-relocation observations.json --out learned.json
reclay inspect-relocation --schema learned.json fresh.bin --start 146 --end 194
reclay relocate --schema learned.json fresh.bin --start 146 --end 194 \
  --payload replacement.bin --out edited.bin
reclay export-relocation --schema learned.json --out relocation-editor.py
python3 relocation-editor.py rebuild fresh.bin replacement.bin edited-again.bin \
  --start 146 --end 194
```

`observations.json` is a list of `{ "file": "sample.bin", "start": 146, "end": 167 }` records. Paths resolve beside that JSON. Mark a span including its intended terminator and excluding alignment padding. Ambiguous schemas require `--model ID`; existing outputs are never overwritten. Spans above are specific to the included experiment, not TIFF knowledge built into the learner.

## Run locally

Python 3.10 or later, no runtime dependencies:

```sh
python3 -m pip install ./reclay-0.3.0-py3-none-any.whl
reclay serve
```

Open the printed loopback address. Load the WAVE, PNG or AIFF observations, infer a model, inspect a block and preview a replacement before downloading it. Your originals are never rewritten. You can also supply your own related files. Ambiguous observations retain their competing explanations; selecting one is an explicit assumption.

From source:

```sh
python3 -m reclay serve
python3 -m unittest discover -s tests -v
```

## What an example proves

The WAVE demonstration contains real FFmpeg-produced files with varying metadata and audio lengths. Change a title to a longer, previously unseen value. Reclay discovers the nested block structure and rebuilds the inner length, padding, enclosing block and outer file length. An independent FFmpeg output with that title is the byte oracle; decoding verifies that the audio remains unchanged.

PNG and AIFF give different structural families: length before identifier with CRC32, versus big-endian identifier before length with aligned blocks. Checks use fresh held-out files and actual independent decoders. These examples exercise the inference mechanism; they are not installed format templates.

## Evidence, assumptions and limits

Reclay searches a declared family of tagged, length-delimited binary layouts. The inference result records source hashes, surviving rules and experiment suggestions. Model hashes identify the exact rule configuration; they are not signatures or authentication of the claimed corpus. Exact round trips establish preservation for those observations. They do not establish the intended meaning of an unknown block, the validity of arbitrary replacements or a complete specification.

Encrypted/compressed payload semantics, arbitrary pointer graphs beyond the marked-span relocation family, bit-packed fields, changing-width object tables and native application behavior are outside this first release. An opaque block stays opaque. A structurally valid file can still be rejected by its original application. Keep that distinction when editing image data, audio headers or unknown application state.

There is no universal format recovery or automatic repair of arbitrary corruption. Contradictory observations, damaged checksums, excessive sizes and unsupported structures are rejected. Boundary experiments matter: short even-length samples cannot distinguish every padding or integer-width rule.

## Prior work

Binary format inference, grammar learning and generated editors have substantial prior work. [Kaitai Struct](https://doc.kaitai.io/serialization.html) already generates reading and writing code from supplied specifications. [ImHex](https://github.com/WerWolv/ImHex/releases) supports pattern-based save editors. [Netzob](https://netzob.github.io/netzob/index.html) supports protocol inference and relationship fields including sizes, padding and CRC32. [BinPRE](https://github.com/ecnusse/BinPRE) recovers protocol fields using executable instrumentation. [FormatFuzzer](https://github.com/uds-se/FormatFuzzer) compiles supplied templates into parsers and generators.

Reclay's contribution is a small complete local workflow: retain alternative rules from raw observations, make the uncertainty actionable, edit supported nested layouts losslessly, and take the inferred codec away as ordinary standalone code. It does not claim the invention of format inference or maintainer endorsement.

The included files were generated for this project by FFmpeg, using deterministic synthetic signals and images. `reclay/examples/manifest.json` records the producer, commands, hashes and held-out identities. No third-party source is embedded in the runtime. Original product code is MIT licensed. Developed and reviewed with Codex.

## Terminal workflow

```sh
reclay infer sample-a.bin sample-b.bin sample-c.bin --out learned.reclay
reclay inspect --schema learned.reclay --model MODEL_ID fresh.bin
reclay edit --schema learned.reclay --model MODEL_ID fresh.bin \
  --path 1/0 --payload replacement.bin --out edited.bin
reclay export --schema learned.reclay --model MODEL_ID --out codec.py
```

Use the studio or the JSON schema to select an actual model ID. A model with unexplained changing prefix bytes refuses size changes. The CLI refuses ambiguous implicit selection and refuses to overwrite an existing output.

The exported codec runs without Reclay installed:

```sh
python3 codec.py inspect fresh.bin
python3 codec.py rebuild fresh.bin edits.json edited.bin
```

The edits file is a list such as `[{"path":[1,0],"payloadHex":"726976657200"}]`. Paths are zero-based; only leaf payloads can be replaced. Changing a known block's interpretation is still your responsibility.

To regenerate the corpus with the recorded FFmpeg version, run `python3 scripts/generate_examples.py`. Generated signals are synthetic and owned; the manifest distinguishes training files from withheld reference files.
