# PDF Workbench

A local-first PDF editor, batch logo stamper, control-panel label extractor and
OCR bench in one application. Everything runs on your machine — no PDF is ever
uploaded anywhere unless you explicitly configure a remote OCR endpoint.

It merges and extends two earlier tools:

| Was | Now |
|---|---|
| `pdf_logo_stamper` (FastAPI + PyMuPDF) | **Logo stamp** mode, same smart whitespace detection, three bugs fixed |
| `antumbrapdfextractor.html` (browser, pdf.js) | **Panels** mode, both layout parsers ported, plus OCR for scanned sheets |
| — | **Edit** mode: a full PDF editor |
| — | **Purchase orders** mode: structured extraction with click-to-copy |

---

## Quick start

```bash
pip install -r requirements.txt
pip install -r requirements-ocr-cpu.txt     # recommended: OCR that works everywhere
python run_app.py
```

The server starts and your browser opens at `http://127.0.0.1:8000`.

---

## The four modes

### Edit
A genuine PDF editor, not a viewer with annotations bolted on.

- **Pages** — merge, split, insert, delete, duplicate, reorder (drag thumbnails),
  rotate, crop, extract.
- **Markup** — highlight, underline, strikeout, sticky notes, freehand ink,
  boxes, circles, arrows and text boxes. These are real PDF annotations, so they
  stay editable in Acrobat and Preview; **Flatten markup** bakes them in.
- **Text & content** — edit text in place (the original font, size and colour are
  matched automatically), erase regions, replace images, and find-and-replace
  across the document.
- **Forms, signatures, redaction** — fill and flatten AcroForm fields, draw or
  place a signature, and perform *true* redaction: the glyphs and image pixels
  are deleted from the content stream and the metadata is scrubbed, not covered
  with a black box.
- **Watermarks and Bates numbering**, and **Make searchable** to add an invisible
  OCR text layer to a scan.
- **Markup manager** — everything already on the current page is listed, so you
  can locate or delete an individual annotation without reaching for undo.

Every operation is undoable (`Ctrl+Z` / `Ctrl+Y`), and a failed operation leaves
the document untouched rather than half-written.

### Viewer
- **Text selection** (`Text` button) puts an invisible selectable layer over the
  page, so you can drag across text and `Ctrl+C` it like any PDF reader.
- **Find** (`Ctrl+F`) searches the whole document, highlights every hit on the
  page and steps through them with Enter / Shift+Enter.
- **Fit** and **Width** zoom modes, plus **Clear all** to close every open PDF
  at once.
- Press **?** for the keyboard-shortcut list.

### Purchase orders
Reads a PO into header fields and a line-item table, then makes every value one
click from the clipboard — click any cell, any header field, the ⧉ button to copy
a whole row, or **Copy table** for the lot as TSV ready to paste into a
spreadsheet. Exports to CSV, TSV, Excel and text.

Parsing is entirely geometric, because PO text extraction order is unreliable —
item codes routinely arrive detached from their row. It handles:

- Column boundaries derived from **whitespace gaps in the table body**, seeded by
  the header positions. Header midpoints alone are not enough: a long description
  overruns the midpoint and lands in the next column.
- **Wrapped column headers** — `DISC`/`%` and `COD`/`E` on two lines recombine
  into `DISC %` and `CODE`.
- **Continuation rows** — description lines carrying no numbers fold back into
  the item above them.
- **Item codes inside the description cell** — both leading (`PD-PCN DyNet…`) and
  trailing parenthesised (`…RECESSED (DUS360-CR)`).
- **Header fields in either orientation** — to the right of the label
  (`Purchase No: 00111797`) or directly beneath it (`Purchase Order Number` over
  `PO-10297`), while ignoring the neighbouring address block that shares the line.
- **Which party is you** — set your company names in Settings, and the *other*
  party is reported as the counterparty.
- **Tables continuing onto later pages** that do not repeat their header. A page
  is only treated as a continuation when it really carries rows, so a
  terms-and-conditions page is never mined for junk items.

**Read all** processes every open PDF in one go and merges the results into a
single table tagged with the order each row came from — useful when a batch of
POs arrives together. Any cell can be **double-clicked to correct** before
export, and Excel receives real numbers it can total rather than text.

If a supplier's layout needs help, **Save this layout as a template**; future POs
matching that supplier reuse the saved columns.

### Panels
The original batch panel extractor, intact: per-label copy chips with sticky
green ticks, automatic detection of Dynalite spec sheets vs Smart Home Works job
packs, identical-configuration grouping with "mark all N done", CSV in the layout
EZcad2's variable-text feature expects, and the <kbd>1</kbd>–<kbd>9</kbd> /
<kbd>N</kbd> shortcuts. Scanned sheets, which used to be a dead end, now route
through OCR.

### Logo stamp
Batch-stamps a logo into genuine whitespace across many PDFs, evaluating
bottom-right → bottom-left → bottom-centre → best-fit void scan, shrinking the
logo if space is tight. Detected content boxes and the target rectangle are drawn
over the page; drag a box to override any page by hand. Apply in place (undoable)
or export a ZIP.

Three fixes over the original:
1. Raster verification used to run **only when a page had no vector obstacles**,
   so scanned pages with a single detected element skipped the pixel check
   entirely. The winning candidate is now always verified.
2. The logo's true aspect ratio is honoured instead of stretching it to the box.
3. One text block with unencodable characters no longer discards a page's
   obstacles.

---

## OCR

OCR is a pluggable layer. The app probes what this machine can do and picks the
best available engine; **Settings** shows each engine, whether it is available,
and exactly why not with the command to fix it.

| Engine | Needs | Notes |
|---|---|---|
| **Embedded PDF text** | nothing | Used automatically whenever a page already has a text layer — instant and exact |
| **DeepSeek-OCR** | NVIDIA GPU, ~8 GB VRAM | Best quality; understands document layout and emits markdown with grounding boxes |
| **Remote endpoint** | a URL | For a GPU box elsewhere on your network |
| **RapidOCR** | `pip install rapidocr-onnxruntime` | CPU, no system packages — the sensible baseline |
| **Tesseract** | the Tesseract binary | Widely available, weakest on dense tables |

### Installing DeepSeek-OCR

```bash
# 1. PyTorch matched to your CUDA version (12.1 shown)
pip install torch --index-url https://download.pytorch.org/whl/cu121
# 2. The rest
pip install -r requirements-ocr-gpu.txt
```

The model (~6.7 GB) downloads on first use into the app workspace. In Settings
you can choose the resolution preset — `gundam` (dynamic tiling) is the default
and handles dense purchase orders best — force a device, or point at a
pre-downloaded model folder for a fully offline install.

**Without an NVIDIA GPU, `auto` will not silently pick CPU** — DeepSeek-OCR on CPU
runs 30 s to several minutes per page, which is not a sensible default. Set the
device to `cpu` in Settings if you want it anyway.

---

## Packaging as a Windows executable

```bash
pip install pyinstaller
python build_exe.py --onefile --with-ocr
```

The build lands in `dist/`. `--with-ocr` bundles the RapidOCR CPU engine so the
executable does real OCR out of the box; the GPU stack is never bundled because
it is multiple gigabytes and machine-specific.

A packaged build writes its workspace to the user's app-data folder
(`%LOCALAPPDATA%\PDFWorkbench` on Windows), so it runs from anywhere.

---

## Layout

```
run_app.py                  Launcher — finds a free port and opens the browser
build_exe.py                PyInstaller build script
src/
  server.py                 FastAPI app assembly
  config.py                 Paths and settings (workspace is always writable)
  session.py                Document store, atomic writes, undo/redo stacks
  models.py                 Pydantic schemas
  exporters.py              CSV / TSV / XLSX / text output
  pdfops/                   pageops · annots · textedit · forms · secure
                            detector · stamper  (whitespace detection + stamping)
  ocr/                      base · registry · deepseek · remote · fallback
                            native · service   (engine abstraction)
  extract/                  textgrid · purchase_order · panels · templates
  routers/                  documents · edit · ocr_routes · extract_routes · stamp
static/                     Vanilla JS front end, no CDN, fully offline
tests/                      103 tests
```

## Tests

```bash
python -m pytest tests/ -q
```

The purchase-order tests are pinned against the two real supplier layouts in
`tests/sample_data/`, asserting exact field and line-item values, so a
regression in the column logic fails loudly.

## Notes and limits

- **Text editing** is redact-and-repaint, which is what PyMuPDF allows: the
  replacement uses the closest base-14 font rather than the document's embedded
  font. Visually very close for ordinary body text; a heavily styled display face
  will not match exactly.
- **Move content** rasterises the region, so it is lossless for logos and stamps
  but will soften vector text.
- **Redaction is irreversible** once saved — that is the point. Undo works within
  the session only.
- Header-field detection on *scanned* POs is weaker than on born-digital ones,
  because it depends on the OCR engine's box accuracy. Line items hold up well;
  DeepSeek-OCR on a GPU improves both.
