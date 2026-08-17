# PDF Workbench

A local-first PDF editor, batch logo stamper, control-panel label extractor,
Antumbra panel designer, job-pack assembler and OCR bench in one application. Everything runs on your machine — no PDF is ever
uploaded anywhere unless you explicitly configure a remote OCR endpoint.

It merges and extends two earlier tools:

| Was | Now |
|---|---|
| `pdf_logo_stamper` (FastAPI + PyMuPDF) | **Logo stamp** mode, same smart whitespace detection, three bugs fixed |
| `antumbrapdfextractor.html` (browser, pdf.js) | **Panels** mode, both layout parsers ported, plus OCR for scanned sheets |
| — | **Edit** mode: a full PDF editor |
| — | **Purchase orders** mode: structured extraction with click-to-copy |
| — | **Designer** mode: configure Antumbra panels, engraving spec, BOM and quote |
| — | **Batch** mode: job packs and tools that run across every open document |

---

## Quick start

```bash
pip install -r requirements.txt
pip install -r requirements-ocr-cpu.txt     # recommended: OCR that works everywhere
python run_app.py
```

The server starts and your browser opens at `http://127.0.0.1:8000`.

---

## The six modes

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

Every operation is undoable (`Ctrl+Z` / `Ctrl+Y`), and a failed operation leaves
the document untouched rather than half-written.

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

If a supplier's layout needs help, **Save this layout as a template**; future POs
matching that supplier reuse the saved columns.

### Panels
The original batch panel extractor, intact: per-label copy chips with sticky
green ticks, automatic detection of Dynalite spec sheets vs Smart Home Works job
packs, identical-configuration grouping with "mark all N done", CSV in the layout
EZcad2's variable-text feature expects, and the <kbd>1</kbd>–<kbd>9</kbd> /
<kbd>N</kbd> shortcuts. Scanned sheets, which used to be a dead end, now route
through OCR.

### Designer
Recreates the Dynalite Design Studio configurator as a local tab, and joins it up
to the rest of the workbench.

Pick the family (AntumbraButton, AntumbraTouch, AntumbraDisplay), the series
(Antumbra or AntumbraLite), the region (Australian/American 75 x 116 mm, or
European 86 x 86 mm) and the button count, then mix and match button and rim
finishes. The panel is drawn live at the millimetres it will actually be made
at; click a button on it to engrave that position with up to two lines of text
and an icon from a 40-icon library covering lighting, scenes, shades, climate,
hotel and media. A **Daylight / Backlit** toggle previews the panel lit, which is
how you catch a label that disappears against a dark fascia.

The product code is assembled from the published Antumbra structure and shown
live — `PA6BPA-WA` is a six-button Australian/American panel with white buttons
and an aluminium rim:

```
PA 6 B P A - W A
│  │ │ │ │   │ └── rim finish     W White · M Magnesium · C Chrome · A Aluminium
│  │ │ │ │   └──── button finish  W S M polycarbonate · A G N V P metallic
│  │ │ │ └──────── region         A Australian/American · E European
│  │ │ └────────── series         P Antumbra · L AntumbraLite
│  │ └──────────── family         B Button · T Touch · D Display
│  └────────────── button count (button families only)
└───────────────── Antumbra range
```

Three outputs:

- **Spec sheet PDF** — one page per panel: a **1:1** front elevation you can
  print at 100% and hold the part against, numbered position callouts, the
  finish block with colour swatches, and the engraving schedule.
- **Schedule CSV** — one row per engraved position with the order details
  repeated, so it reads as both a laser schedule and a checkable order line.
- **Send to Panels queue** — hands the job to **Panels** mode, expanded to one
  entry per physical panel, where it picks up the copy chips, the
  identical-configuration grouping and the EZcad2 export.

**Label templates.** Save a panel's labels once and apply them to the next
panel of the same room type. Templates are kept on this machine and offered on
every job; a template with more labels than the target panel has buttons drops
the surplus and says so.

**Job costing.** *Price this job* groups identical configurations into order
lines — two suites of the same panel become one line of 20 — adds an engraving
line covering every engraved panel, and totals with tax. Rates come from a
local price book you can add to as you go; anything with no rate is carried at
zero and flagged both on screen and on the quote, so an unpriced part is
visible rather than silently free. Exports as a quotation PDF, CSV or Excel.

Jobs save and reopen as JSON, so a design can be revisited when the client
changes a label.

The Signify **12NC** ordering number is deliberately *not* generated. It is
allocated by Signify rather than derived from the configuration, so the designer
carries it as a field you fill in from your quote — inventing one would put a
wrong number on a purchase order.

### Batch
Everything else in the workbench works on one document; this tab works on all
of them.

**Job pack** assembles the selected PDFs into one issued deliverable: a cover
sheet carrying project, client, reference and revision, a contents page whose
rows are real links, PDF bookmarks matching it, continuous page numbering and a
title-block footer. Each source keeps its own outline, rebased onto the pack.
The running order is shown live — which documents are in, their title in the
contents, and the page each starts on once the front matter is counted — so the
preview and the built pack agree before anything is generated.

**Bulk tools** run over every selected document: page numbers and footer,
watermark, rotate, optimise, flatten annotations, strip metadata, split by page
count or ranges, and rename by pattern (`{name} {n} {nn} {pages} {date}
{project} {rev}`). Each either downloads as a ZIP, leaving the originals alone,
or applies in place — where every document keeps its own undo, so an eight-file
batch is eight separate undos, not one.

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
  designer/                 catalogue · icons · render · jobs · bom
                            (Antumbra products, geometry, spec sheets, quoting)
  assemble/                 pack · batch
                            (job packs and cross-document operations)
  routers/                  documents · edit · ocr_routes · extract_routes
                            stamp · designer_routes · batch_routes
static/                     Vanilla JS front end, no CDN, fully offline
tests/                      149 tests
```

## Tests

```bash
python -m pytest tests/ -q
```

The purchase-order tests are pinned against the two real supplier layouts in
`tests/sample_data/`, asserting exact field and line-item values, so a
regression in the column logic fails loudly.

The designer tests assert the part codes against real published examples, check
that no button escapes its plate, and render every family/series/region
combination in the catalogue — a finish that cannot be drawn would otherwise
become a broken order.

The assembly tests pin the page-offset arithmetic: that a contents row links to
the page it names, that each source's own bookmarks land rebased, and that a
batch applied in place stays undoable per document.

Tests never touch the real settings file — an autouse fixture points it at a
temporary path, so the price book and saved templates are safe to run against.

## Notes and limits

- **Text editing** is redact-and-repaint, which is what PyMuPDF allows: the
  replacement uses the closest base-14 font rather than the document's embedded
  font. Visually very close for ordinary body text; a heavily styled display face
  will not match exactly.
- **Move content** rasterises the region, so it is lossless for logos and stamps
  but will soften vector text.
- **Redaction is irreversible** once saved — that is the point. Undo works within
  the session only.
- **Antumbra geometry** in the designer is drawing-accurate, not a manufacturing
  drawing: plate sizes and the button grid come from the published dimensions,
  but tolerances, cut-outs and fixing centres are not modelled. Check a first-off
  against the part.
- Header-field detection on *scanned* POs is weaker than on born-digital ones,
  because it depends on the OCR engine's box accuracy. Line items hold up well;
  DeepSeek-OCR on a GPU improves both.
