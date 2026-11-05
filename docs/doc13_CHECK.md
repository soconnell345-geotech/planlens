# DOC-13 check: planlens public docs

Documentation only; no code, no new corpus number. Every pinned figure is untouched.

| Stale line (backlog ref; line numbers had drifted) | Now says | Evidence |
|---|---|---|
| README "Primitive layer": raster images "`raster` extra" | OpenCV is a core dependency, no extra needed | `pyproject.toml` dependencies list `opencv-python-headless`, `rapidfuzz`; `raster = []`, `text = []` |
| README Install: extras | OpenCV and rapidfuzz core; `[raster]`/`[text]` empty aliases | same |
| README silent on 0.12-0.14 | New "What changed since 0.11" (0.12 marks, numpy `find_like`; 0.13 visual scales; 0.14 plan locations, samples, etc.) | CHANGELOG 0.12.0, 0.13.0, 0.14.0 |
| README OCR recipe wrong on FIPS hosts | New "Hosts where OpenCV cannot load"; the `--no-deps` recipe is labelled for hosts where OpenCV loads, and OCR is stated unavailable on FIPS hosts | `planlens/opencv.py` (child-process probe, `PLANLENS_CV2_PROBE`); `ocr.py:175` and `ir/raster.py:_require_cv2` call `opencv.load()`, which raises `ImportError`; `findlike.resolve_backend` falls back to numpy; `document/raster.py` docstring (numpy + PyMuPDF only) |
| `document/DESIGN.md` module map stopped at 0.4 | Map lists scales, scalefinder, measuring, raster, roles, loggrid, planlocation, findlike, budget; notes `opencv.py`, `pymupdf_settings.py` | `ls planlens/document`, module docstrings |
| no scales section | New "Visual scales (0.13.0)" | `scales.py`, `scalefinder.py`, `measuring.py`, `raster.py` docstrings and signatures; CHANGELOG 0.13.0 |
| Two vs four sheets (units stated nowhere) | One bullet: four sheets, unit recovered from prose on two, the other two are the table's "unit right 6/8" | Arithmetic on the existing table and bullet text, **not from code**: the sheets are a private corpus and the code cannot count them. Only existing numbers used. |
| Stale Azure line ("No live Azure result has been run through it yet") | Replaced: unit tests use hand-built results; real results feed the corpus measurements | the same doc's "Page roles" section says some corpus reports were read through Azure DI |
| `ir/DESIGN.md` 13 tools | 17 tools | `python -c "from planlens.tools import ReviewToolkit; print(len(ReviewToolkit().specs()))"` printed 17 (adds `find_like`, `measure`, `plan_symbols`, `plan_grid`) |

The app's CLAUDE.md says "15 tools at 0.13.0"; that was true then and is not edited here (other repo).

## Tests
`pytest planlens/tests/test_readme_claims.py planlens/tests/test_packaging.py planlens/document/tests/test_quantities.py planlens/document/tests/test_search_fuzzy.py`: 101 passed, 0 failed, exit 0.

## Noted, not fixed
- `document/DESIGN.md` "Not built yet" still lists items I did not check against the code.
- The "6/8" reconciliation relies on the doc's own account of the private corpus; the owner may want to confirm.
