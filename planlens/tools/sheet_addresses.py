"""Sheet numbers as addresses (BACKLOG PL-37).

"Check the callouts on S-201" left the model to guess which page S-201 is.
With the sheet tools served (``ReviewToolkit(sheet_tools=True)``, or a host
layer's call made under :class:`planlens.tools.toolkit.sheets_at_open`), a
tool that takes ``pages`` also takes ``sheets``, and one that takes ``page``
also takes ``sheet``: printed sheet numbers, turned into the pages the sheet
index (:meth:`planlens.document.Document.sheet_index`) reads them on before
the tool runs.

The rule is the index's own (:func:`planlens.document.sheets.
normalize_number`): case and spaces aside, a number must be the number read.
"S201" is not taken for "S-201"; the index's own form is named and the call
refused. A number the index holds on more than one page, or not at all, is
refused with words that say so; nothing is guessed.

A toolkit that does not serve the sheet tools is offered, and answers, what
it was: its specs carry no such argument, and a call that gives one is
refused as an unknown argument, as before.

Pure Python over the index; no third-party import.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from planlens.document.sheets import normalize_number

#: The argument a sheet number is given in, by the page argument it stands
#: for.
SHEET_ARGS: Dict[str, str] = {"pages": "sheets", "page": "sheet"}

#: What the model is told of ``sheets``.
SHEETS_DESCRIPTION = (
    "Printed sheet numbers instead of pages, as the sheet index reads them "
    "from the title blocks: one ('S-201') or several, comma-separated "
    "('S-201, A-101'). A number the index does not hold, or holds on more "
    "than one page, is refused, never guessed.")

#: What the model is told of ``sheet``.
SHEET_DESCRIPTION = (
    "A printed sheet number instead of page ('S-201'), as the sheet index "
    "reads it from the title blocks. A number the index does not hold, or "
    "holds on more than one page, is refused, never guessed.")

#: The toolkit's schema of each argument.
SHEETS_SCHEMA = {"description": SHEETS_DESCRIPTION,
                 "anyOf": [{"type": "string"},
                           {"type": "array", "items": {"type": "string"}}]}
SHEET_SCHEMA = {"type": "string", "description": SHEET_DESCRIPTION}

#: How many of the index's numbers a refusal lists.
LISTED = 40

#: Said with every refusal: the ways round it.
OTHER_WAYS = ("sheet_index lists every sheet with its page; or give pages "
              "(0-based) or pdf_pages (1-based, as a PDF viewer shows)")


def with_sheet_args(spec: Dict[str, Any]) -> Dict[str, Any]:
    """A copy of tool ``spec`` (``name`` / ``description`` / ``parameters``)
    with ``sheets`` beside ``pages`` and ``sheet`` beside ``page``, and
    ``page`` no longer required where ``sheet`` may stand for it. ``spec``
    itself, untouched, when it takes neither."""
    props = (spec.get("parameters") or {}).get("properties") or {}
    if not any(arg in props for arg in SHEET_ARGS):
        return spec
    out = copy.deepcopy(spec)
    params = out["parameters"]
    if "pages" in props:
        params["properties"]["sheets"] = copy.deepcopy(SHEETS_SCHEMA)
    if "page" in props:
        params["properties"]["sheet"] = copy.deepcopy(SHEET_SCHEMA)
        if "required" in params:
            params["required"] = [r for r in params["required"]
                                  if r != "page"]
    return out


def given(value: Any) -> bool:
    """Whether a sheet argument was given at all (``None``, ``""`` and an
    empty list are not)."""
    return value not in (None, "", [], ())


def split_sheets(value: Any) -> Tuple[List[str], Optional[str]]:
    """``(numbers, None)``: the sheet numbers ``value`` names, in order, each
    once -- a string (comma- or semicolon-separated), a whole number (its
    digits: sheet 201) or a list of those; or ``([], problem)``."""
    items = value if isinstance(value, (list, tuple)) else [value]
    out: List[str] = []
    for item in items:
        if isinstance(item, bool) or not isinstance(item, (str, int)):
            return [], (f"sheet numbers are text such as 'S-201', not "
                        f"{item!r}")
        for part in re.split(r"[,;]", str(item)):
            part = part.strip()
            if part and normalize_number(part) not in {
                    normalize_number(n) for n in out}:
                out.append(part)
    if not out:
        return [], "no sheet number was given"
    return out, None


def _bare(number: str) -> str:
    """Letters and digits only: how a near form is NAMED, never taken."""
    return re.sub(r"[^0-9A-Z]", "", normalize_number(number))


def pages_text(pages: Sequence[int]) -> str:
    """"pages 2 and 7 (pdf_pages 3 and 8)"."""
    def join(values):
        values = [str(v) for v in values]
        return (values[0] if len(values) == 1
                else ", ".join(values[:-1]) + " and " + values[-1])
    word = "page" if len(pages) == 1 else "pages"
    return (f"{word} {join(pages)} (pdf_{word} "
            f"{join([p + 1 for p in pages])})")


def _listed(numbers: Sequence[str]) -> str:
    shown = ", ".join(numbers[:LISTED])
    more = len(numbers) - LISTED
    return shown + (f" and {more} more" if more > 0 else "")


def resolve(index: Any, wanted: Sequence[str], arg: str = "sheets"
            ) -> Tuple[List[Tuple[str, int]], Optional[Dict[str, str]]]:
    """``(found, None)``: for each number of ``wanted``, in order, the number
    as sheet ``index`` (a :class:`planlens.document.sheets.SheetIndex`)
    reads it and the 0-based page it reads it on; or ``([], refusal)`` --
    ``{"error", "hint"}`` -- when one is not in the index or is read on more
    than one page."""
    held: Dict[str, List[int]] = {}
    printed: Dict[str, str] = {}
    for row in index.indexed:
        key = normalize_number(row.number)
        held.setdefault(key, []).append(row.page)
        printed.setdefault(key, row.number)
    asked = ", ".join(wanted)
    if not held:
        why = "; ".join(index.notes) or "no title block was read"
        return [], {
            "error": (f"{arg} {asked}: this document's sheet index holds no "
                      f"sheet numbers ({why}), so no page can be found by "
                      f"its sheet number"),
            "hint": "give pages (0-based) or pdf_pages (1-based, as a PDF "
                    "viewer shows) instead"}
    missing = [w for w in wanted if normalize_number(w) not in held]
    if missing:
        what = (f"sheet {missing[0]} is" if len(missing) == 1
                else f"sheets {', '.join(missing)} are")
        error = (f"{what} not in this document's sheet index, which reads "
                 f"{len(printed)} sheet number"
                 f"{'' if len(printed) == 1 else 's'}: "
                 f"{_listed(list(printed.values()))}")
        unread = [r.page for r in index.not_indexed]
        if unread:
            error += (f"; {len(unread)} sheet"
                      f"{' was' if len(unread) == 1 else 's were'} not "
                      f"indexed ({pages_text(unread[:12])}"
                      f"{' and more' if len(unread) > 12 else ''}), and it "
                      f"may be among them: look at them")
        near = []
        for w in missing:
            forms = [printed[k] for k in printed if _bare(k) == _bare(w)]
            if forms:
                near.append(f"{w} -> {' or '.join(forms)}")
        hint = OTHER_WAYS
        if near:
            hint = ("the index reads a number of the same letters and "
                    "digits, printed otherwise (" + "; ".join(near)
                    + "): give the number as printed if that is the sheet "
                    "meant; " + OTHER_WAYS)
        return [], {"error": error, "hint": hint}
    doubled = [(w, held[normalize_number(w)]) for w in wanted
               if len(held[normalize_number(w)]) > 1]
    if doubled:
        said = "; ".join(f"sheet {w} is read on {pages_text(pages)}"
                         for w, pages in doubled)
        return [], {
            "error": (f"{said}: one sheet number on more than one page is "
                      f"not taken for either"),
            "hint": ("give pages (0-based) or pdf_pages for the one meant; "
                     "sheet_index shows each page's title and revision")}
    return [(printed[normalize_number(w)], held[normalize_number(w)][0])
            for w in wanted], None


__all__ = ["SHEET_ARGS", "SHEETS_DESCRIPTION", "SHEET_DESCRIPTION",
           "SHEETS_SCHEMA", "SHEET_SCHEMA", "OTHER_WAYS", "with_sheet_args",
           "given", "split_sheets", "pages_text", "resolve"]
