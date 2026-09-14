"""Document structure from what the pages themselves print.

A stapled submittal is several documents: a transmittal, a review letter, a
drawing set, a calculation package with a report nested inside it, appendices.
Nobody wrote a table of contents for the stapled whole, but every constituent
carries its own running header or footer and its own printed page numbers —
and those change exactly where one document ends and the next begins. This
module reads them:

- the **header** and **footer** bands of each page (top and bottom 8%);
- the **printed page number** ("Page 15 of 245", "Page 7") and **sheet
  reference** ("SHEET 1 OF 7") found there;
- **divider pages** — a cover or tab whose heading says APPENDIX, ATTACHMENT,
  ITEM, SECTION, CONTENTS, ...;
- **duplicates** — a page whose text and linework repeat an earlier page;
- and from these, **segments**: runs of consecutive pages that share a running
  header/footer and numbering, split at dividers, at a change of page size or
  between drawing sheets and everything else, and where the printed page
  number restarts at 1 (a nested document).

Measured on a real 260-page submittal, the footers alone separate the drawing
set, the checked calculation package (its stamp counts 1-245) and the
narrative report embedded in it (its own "Page 1-13").
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

from planlens.document.model import PageSummary, TextLine

#: Fraction of the page height that counts as the header / footer band.
BAND_FRACTION = 0.08

#: Longest header / footer text kept on a summary.
BAND_MAX_CHARS = 120

_PAGE_OF = re.compile(r"\bpage\s+(\d{1,4})(?:\s+of\s+(\d{1,4}))?\b", re.I)
_SHEET_OF = re.compile(r"\bsheet\s+(\d{1,3})\s+of\s+(\d{1,3})\b", re.I)
_SHEET_ID = re.compile(
    r"\b(?:sheet|sht|dwg)\.?\s*(?:no\.?\s*)?([A-Z]{1,2}-?\d{1,3}(?:\.\d{1,2})?)\b",
    re.I)
_DIVIDER = re.compile(
    r"^(appendix|attachment|addendum|annex|exhibit|item|section|part|tab|"
    r"chapter|volume|schedule|enclosure|cover|contents|index|transmittal|"
    r"figures|tables|plates|photographs|calculations|drawings|"
    r"specifications)\b", re.I)

#: Shortest running key that may match another by containment (a package's
#: page stamp is a suffix of the longer footers printed inside it).
_CONTAIN_MIN = 12
_DIVIDER_WHOLE = {"table of contents", "contents", "cover", "cover sheet",
                  "transmittal", "letter of transmittal", "index", "drawings",
                  "calculations", "specifications", "figures", "tables",
                  "photographs", "boring logs", "back to toc page"}

#: Pages with fewer words than this can be dividers.
DIVIDER_MAX_WORDS = 60


def _norm_key(text: str) -> str:
    """Digits masked, case and whitespace folded, purely numeric fragments
    dropped — what stays the same from one page of a document to the next.
    (A boring log's footer band catches stray depth numbers; a stamp does
    not change.)"""
    parts = []
    for frag in text.split("|"):
        frag = frag.strip()
        if sum(ch.isalpha() for ch in frag) >= 3:
            parts.append(re.sub(r"\d", "#", frag).lower())
    return re.sub(r"\s+", " ", " | ".join(parts)).strip()


def _same_running(a: str, b: str) -> bool:
    """Equal keys, or one contains the other (a package stamp inside a
    longer footer)."""
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= _CONTAIN_MIN and short in long_


def band_texts(lines: Sequence[TextLine], height: float
               ) -> Tuple[List[str], List[str]]:
    """Text lines in the top and bottom bands, in reading order."""
    top, bottom = [], []
    for ln in lines:
        if ln.bbox is None:
            continue
        if ln.bbox[3] <= BAND_FRACTION * height:
            top.append(ln)
        elif ln.bbox[1] >= (1.0 - BAND_FRACTION) * height:
            bottom.append(ln)
    key = lambda l: (round(l.bbox[1] / 4.0), l.bbox[0])   # noqa: E731
    return ([l.text for l in sorted(top, key=key)],
            [l.text for l in sorted(bottom, key=key)])


def printed_numbers(texts: Sequence[str]) -> Dict[str, Any]:
    """Printed page / sheet numbers in header+footer text."""
    joined = " ".join(texts)
    out: Dict[str, Any] = {}
    best = None
    for m in _PAGE_OF.finditer(joined):
        cand = (int(m.group(1)), int(m.group(2)) if m.group(2) else None)
        # "Page N of M" beats a bare "Page N" (the stamp that spans a package).
        if best is None or (cand[1] is not None and best[1] is None):
            best = cand
    if best is not None:
        out["printed_page"] = best[0]
        if best[1] is not None:
            out["printed_of"] = best[1]
    m = _SHEET_OF.search(joined)
    if m is None and "sheet" in joined.lower():
        m = re.search(r"\b(\d{1,3})\s+of\s+(\d{1,3})\b", joined, re.I)
    if m:
        out["sheet"] = f"{int(m.group(1))} of {int(m.group(2))}"
    else:
        m = _SHEET_ID.search(joined)
        if m:
            out["sheet"] = m.group(1).upper()
    return out


def divider_title(heading: Optional[str], n_words: int,
                  first_lines: Sequence[str]) -> Optional[str]:
    """The title of a divider / tab / cover page, else None."""
    if n_words > DIVIDER_MAX_WORDS:
        return None
    candidates = [heading] if heading else []
    candidates += list(first_lines[:3])
    for text in candidates:
        if not text:
            continue
        t = " ".join(text.split())
        low = t.lower().strip(" :.-")
        if low in _DIVIDER_WHOLE or _DIVIDER.match(t):
            # A cover's name is its largest text ("Project Alpha") even when
            # the word that made it a divider sits in a subtitle ("Cover").
            return (" ".join(heading.split()) if heading else t)[:100]
    return None


def content_hash(kind: str, lines: Sequence[TextLine], n_paths: int) -> str:
    h = hashlib.sha1()
    h.update(kind.encode())
    h.update(str(n_paths).encode())
    for ln in lines:
        h.update(ln.text.encode("utf-8", "replace"))
        h.update(b"\n")
    return h.hexdigest()[:16]


def _size_class(s: PageSummary) -> str:
    area = (s.width / 72.0) * (s.height / 72.0)
    if area >= 250.0:
        return "large"
    return "tabloid" if area >= 150.0 else "letter"


def _strip_numbering(text: str) -> str:
    t = _PAGE_OF.sub("", text)
    t = _SHEET_OF.sub("", t)
    t = re.sub(r"\s*\|\s*(\|\s*)+", " | ", t)
    return t.strip(" |")


def segments(summaries: Sequence[PageSummary]) -> List[Dict[str, Any]]:
    """Runs of pages that belong to the same constituent document."""
    runs: List[List[PageSummary]] = []
    prev: Optional[PageSummary] = None
    for s in summaries:
        new = prev is None
        if prev is not None:
            hk, fk = _norm_key(s.header or ""), _norm_key(s.footer or "")
            phk, pfk = _norm_key(prev.header or ""), _norm_key(prev.footer or "")
            same_running = _same_running(hk, phk) or _same_running(fk, pfk)
            both_sheets = (s.kind == "drawing_sheet"
                           and prev.kind == "drawing_sheet")
            if s.divider_title:
                new = True
            elif _size_class(s) != _size_class(prev):
                new = True
            elif (s.kind == "drawing_sheet") != (prev.kind == "drawing_sheet"):
                new = True
            elif both_sheets:
                # A drawing set's bands are full of labels that differ sheet
                # to sheet; consecutive sheets of one size are one set.
                new = False
            elif (s.printed_page == 1 and prev.printed_page is not None
                  and prev.printed_page != 1 and not same_running):
                new = True
            elif (hk or fk) and not same_running:
                new = True
            elif not (hk or fk) and (phk or pfk) and s.kind != "blank":
                new = True
        if new:
            runs.append([s])
        else:
            runs[-1].append(s)
        prev = s

    out: List[Dict[str, Any]] = []
    for i, run in enumerate(runs):
        first = run[0]
        headers = Counter(_norm_key(s.header) for s in run if s.header)
        footers = Counter(_norm_key(s.footer) for s in run if s.footer)
        header = next((s.header for s in run if s.header
                       and _norm_key(s.header) == headers.most_common(1)[0][0]),
                      None) if headers else None
        footer = next((s.footer for s in run if s.footer
                       and _norm_key(s.footer) == footers.most_common(1)[0][0]),
                      None) if footers else None
        kinds = Counter(s.kind for s in run)
        sheet_refs = [s.sheet for s in run if s.sheet]
        if first.divider_title:
            title = first.divider_title
        elif set(kinds) == {"drawing_sheet"}:
            title = "Drawing sheets"
            if sheet_refs:
                title += f" {sheet_refs[0]}" + (
                    f" .. {sheet_refs[-1]}" if len(sheet_refs) > 1 else "")
            elif first.label:
                title += f" {first.label}" + (
                    f" .. {run[-1].label}" if run[-1].label and len(run) > 1
                    else "")
        elif header:
            title = _strip_numbering(header)
        elif footer and _strip_numbering(footer):
            title = _strip_numbering(footer)
        elif first.heading:
            title = first.heading
        else:
            title = kinds.most_common(1)[0][0]
        printed = [s.printed_page for s in run if s.printed_page is not None]
        seg: Dict[str, Any] = {
            "id": i,
            "pages": (f"{first.page}-{run[-1].page}" if len(run) > 1
                      else str(first.page)),
            "n_pages": len(run),
            "title": title[:100],
            "kinds": dict(kinds.most_common()),
        }
        if first.heading and first.heading != title:
            seg["first_heading"] = first.heading[:100]
        if printed:
            seg["printed_pages"] = (f"{min(printed)}-{max(printed)}"
                                    if len(printed) > 1 else str(printed[0]))
            of = [s.printed_of for s in run if s.printed_of]
            if of:
                seg["printed_of"] = Counter(of).most_common(1)[0][0]
        if header:
            seg["header"] = header[:BAND_MAX_CHARS]
        if footer:
            seg["footer"] = footer[:BAND_MAX_CHARS]
        if sheet_refs:
            seg["sheets"] = sheet_refs[:20]
        out.append(seg)
        for s in run:
            s.segment = i
    return out
