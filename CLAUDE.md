# Working on PDF Workbench

A local-first PDF workbench for an electrical contractor doing Dynalite /
Antumbra control panels: editing, purchase-order extraction, panel-label
extraction, panel design and engraving specs, job packs, batch document work
and OCR. Everything runs on the user's machine; no document leaves it unless a
remote OCR endpoint is configured explicitly.

## Commands

```bash
pip install -r requirements.txt            # core
pip install -r requirements-ocr-cpu.txt    # optional CPU OCR

python run_app.py                          # launch (finds a free port, opens a browser)
python -m uvicorn src.server:app --reload --port 8000   # dev server
python -m pytest tests/ -q                 # the whole suite, ~3 seconds
python build_exe.py                        # PyInstaller build
```

Use `--reload` when developing. Plain `uvicorn` will not pick up a new route,
and the resulting 404 looks like a front-end bug for a good ten minutes.

## Shape of the code

```
src/
  server.py        FastAPI assembly — every router must be registered here
  config.py        Paths and settings; DEFAULT_SETTINGS is a whitelist
  session.py       Document store, atomic writes, undo/redo
  models.py        All Pydantic schemas, one file
  exporters.py     Every CSV / XLSX / text export
  pdfops/          Single-document PDF operations
  ocr/             Engine abstraction and registry
  extract/         Purchase orders, panel sheets, templates
  designer/        catalogue · icons · render · jobs · bom  (Antumbra)
  assemble/        pack · batch  (job packs, cross-document work)
  routers/         One module per feature area
static/
  index.html       Tab buttons, stage elements, script tags
  css/app.css      Design tokens then components then console furniture
  js/app.js        Shell: document tabs, mode registry, global keys
  js/mode-*.js     One module per tab, each with activate/deactivate/refresh
tests/             Pytest; conftest.py holds the fixtures
```

## Conventions

Match what is already there rather than importing outside habits.

- **Comments explain why, not what.** The existing code comments the
  non-obvious decision and stays silent on the obvious line. Keep that ratio.
- **Errors are the user's problem to fix, phrased as such.** `raise
  ValueError("That split would produce nothing")`, not `"invalid input"`.
  `server.py` turns `ValueError` into a 400 with the message intact.
- **British spelling** in user-facing strings and identifiers (`colour`,
  `optimise`, `centre`) — the codebase is consistent about it.
- **No new dependencies** without a strong reason. The app has to keep working
  offline and packaged as a single executable.
- Front end is vanilla JS in IIFE modules on `window`, no build step, no CDN.
  Use the `UI.el` / `UI.field` / `UI.select` helpers.

## Things that will bite you

Each of these cost real debugging time. They are commented at the site too.

1. **`insert_textbox` silently drops the whole string** when the rectangle is a
   fraction too short — no exception, it just returns a negative number and
   draws nothing. Use the `_line` / `_put` helpers (generous height), or
   `insert_text` for single lines, which places a baseline and cannot fail this
   way.
2. **Text fitted to an exact width can still wrap**, and a wrapped line
   overflows its box and is dropped in full. `_fit_size` targets ~96% of the
   available width for this reason.
3. **A GOTO link needs its target page to already exist.** The job pack
   reserves blank contents sheets, inserts the body, *then* draws the contents
   onto them.
4. **Base-14 PDF fonts cannot encode em-dashes or `·`.** They render as `?`.
   Keep PDF strings ASCII; the em-dash is fine in the browser.
5. **`DEFAULT_SETTINGS` in `config.py` is a whitelist.** `save_settings` drops
   any key not listed, so a new setting must be added there or it silently
   never persists.
6. **Tests must never touch the real settings file** — it holds the price book
   and saved templates. The autouse `isolated_settings` fixture in
   `conftest.py` points it at a temp path. Do not bypass it.
7. **The `hidden` attribute loses to a class rule** that sets `display: flex`.
   Any stage element toggled via `hidden` needs an explicit
   `.thing[hidden] { display: none; }`.

## Rules that are deliberate, not accidental

Do not "fix" these without asking:

- **The Signify 12NC is never generated.** It is allocated by Signify, not
  derivable from a configuration. Inventing one puts a wrong number on a
  purchase order. It is a field the user fills in from their quote.
- **Unpriced parts are flagged, never guessed.** A part with no rate in the
  price book carries at zero and is called out on screen and on the quote.
  Silent zero-pricing under-quotes a job.
- **Panel geometry lives once**, in `designer/catalogue.py`. The browser SVG
  preview and the PyMuPDF drawing both read it, which is why the screen and the
  exported sheet cannot drift. Never hardcode a millimetre in JavaScript.
- **Exported documents do not use the screen palette.** Spec sheets, packs and
  quotations go to clients and onto a workshop bench, so they stay
  print-friendly and survive a monochrome laser printer. The neon is for the
  interface only.
- **Batch operations applied in place keep per-document undo.** Eight files is
  eight separate undos, not one. Do not collapse that.

## Adding things

**A tab** — create `static/js/mode-X.js` returning
`{ id, activate(host), deactivate(), refresh(host) }`; add the button to
`index.html`, the script tag, an entry in `MODES` in `app.js`, and if it takes
over the stage, a `body[data-mode=X]` CSS block hiding the PDF furniture plus a
stage element that honours `hidden`.

**An API area** — a module in `src/routers/`, models in `src/models.py`,
registered in `src/server.py`.

**An Antumbra finish, family or icon** — a line in the tables in
`designer/catalogue.py`, or a definition in `designer/icons.py`. It flows to the
preview, the PDF and the ordering code at once. Only add a finish whose code
letter you can confirm; a wrong code becomes a wrong order.

**An export format** — a function in `src/exporters.py`, then the `fmt` literal
in the relevant request model and a branch in the route. Every spreadsheet
export is asserted cell by cell in the tests.

**A batch operation** — a `bytes -> bytes` function in `assemble/batch.py`, add
it to `_HANDLERS` and to the `operation` literal in `models.py`.

## Testing

Write tests that would fail if the behaviour regressed, not tests that restate
the implementation. The suite already pins: part codes against real published
examples, that no button escapes its plate, that every catalogue combination
renders, the job pack's page-offset arithmetic, that contents rows link to the
page they name, and that in-place batches stay undoable.

Run the full suite before committing. It takes about three seconds; there is no
excuse for pushing red.

## Git

Develop on a `claude/*` branch, never commit straight to `main`. Commit
messages: a short imperative subject, then prose explaining *why* and anything
a future reader would trip over. Do not open a pull request unless asked.
