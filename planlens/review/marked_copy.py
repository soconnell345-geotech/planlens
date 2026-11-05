"""Writing, removing, placing and checking markups on a COPY of a document.

Moved from the GeotechStaffEngineer app's ``document_tools`` module
(BACKLOG PL-29 (a)). ``annotate_document`` is the one tool that WRITES: its
``output_path`` is resolved into the conversation's working folder
(:func:`markup_output_path`, ``ReviewHost.working_folder``), every comment is
signed by the host (``ReviewHost.signer``), the copy is written to a
temporary file and swapped in (:func:`write_marked_copy`), and one mark can
be removed or replaced by its id (:func:`remove_markups`). Marks on an IMAGE
file are taken in its own pixels (the host decodes images:
``ReviewHost.images``), and a mark centred off its image or page is refused
before anything is written.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional

from planlens.review import conversation as _conv


def _host():
    from planlens.review.host import current
    return current()


def _document_name(handle: str) -> Optional[str]:
    """What the open document behind ``handle`` was called, if anything is."""
    if not handle:
        return None
    getter = getattr(_conv._toolkit(), "document_name", None)
    name = None
    if getter is not None:       # a planlens older than the accessor has none
        try:
            name = getter(handle)
        except Exception:        # pragma: no cover - a stale handle
            name = None
    if name is None:             # closed since: the name it was opened under
        src = (_conv.handle_source(handle) if str(handle).startswith("doc_")
               else handle)          # A7: a file name given as the handle
        name = os.path.basename(src.replace("\\", "/")) if src else None
    return name


def markup_output_path(output_path: str = "", handle: str = "") -> str:
    """Where a marked-up copy goes: this conversation's working folder.

    planlens is framework-neutral and writes ``output_path`` as given, so the
    app resolves it the way every other tool output is resolved — a bare name
    lands in the working folder this conversation writes to (where it gets a
    download card), an absolute path is honoured. With no name at all the copy is named after the document it
    marks up, which is what a reviewer expects to find beside the original.
    While a host has bound a working folder, an absolute path outside it
    keeps its file name and lands in the working folder (A2: a tool never
    writes into another conversation's folder).
    """
    from planlens.review.host import into_working_folder
    name = str(output_path or "").strip()
    if not name:
        stem = os.path.splitext(_document_name(handle) or "document")[0]
        name = f"{stem}_marked.pdf"
    if not os.path.splitext(name)[1]:
        name += ".pdf"
    folder = _host().folder()
    if folder:
        return into_working_folder(name, folder)
    return _host().out_path(name)




# ---------------------------------------------------------------------------
# Writing a marked-up copy: never over a file in use, one mark at a time
# ---------------------------------------------------------------------------
# Live smoke wave 2b, C4 (F31): to change one comment the model rebuilt all 19
# marks with append=false onto review_set_marked.pdf, which this
# conversation's toolkit held OPEN (the model had opened the marked copy to
# read its marks). MuPDF saved straight onto it; Windows refused to remove a
# file in use and the turn died. On Linux the save succeeds and the open
# handle silently keeps serving the old marks; a save that fails midway
# leaves the delivered copy broken. Now the copy is written to a temporary
# file beside it and swapped in with os.replace, after every handle this
# conversation holds on it is closed (and reopens, by name, on next use);
# and one mark can be removed or replaced by its id without rebuilding the
# rest.

#: How a markup id that planlens' document_markups lists looks ("p0.m1").
_MARKUP_ID = re.compile(r"^p(\d+)\.m(\d+)$")


def _temp_beside(final: str) -> str:
    """A temporary file name for writing ``final``: in the conversation's
    scratch folder when ``final`` is in the working folder (never a
    download card, never mirrored), else beside it as a dot-file.

    The name is SHORT -- ``<8 hex>.part.pdf``, no stem (live smoke wave 2c,
    D5): ``.scratch\\<the final name>.<8 hex>.part.pdf`` added 27
    characters to an already long name and pushed the path past Windows'
    260-character limit (F46 at 265, F16), where MuPDF could not open it."""
    import uuid
    tag = uuid.uuid4().hex[:8]
    folder = os.path.dirname(os.path.abspath(final))
    try:
        host = _host().folder()
        if host and os.path.normcase(os.path.abspath(host)) == \
                os.path.normcase(folder):
            folder = os.path.join(folder, _host().scratch_dir)
            os.makedirs(folder, exist_ok=True)
            return os.path.join(folder, f"{tag}.part.pdf")
    except Exception:  # noqa: BLE001 - beside it, then
        pass
    return os.path.join(folder, f".{tag}.part.pdf")


#: Longest full path Windows opens without long-path support.
WINDOWS_MAX_PATH = 260


class MarkedCopyNotWritten(OSError):
    """The marked copy could not be written on the server: a file-system
    failure, not a problem with the marks, so the same call fails again."""


#: The hint a write failure carries (D5: F16 retried the same call five
#: times on the generic "try it again once").
WRITE_FAILED_HINT = (
    "This is a file problem on the server, not a problem with the marks: "
    "calling annotate_document again the same way will fail the same way, "
    "and check=false will not help. If the cause says the path is too long, "
    "a shorter output_path may work, once; otherwise tell the user plainly "
    "that the marked copy could not be saved, with the cause, and stop "
    "retrying.")


def _write_failure(path: str, cause: Any = None) -> MarkedCopyNotWritten:
    """A :class:`MarkedCopyNotWritten` naming what failed and, where it can
    be told, why -- a path too long for Windows is said as such, since
    MuPDF's own message is cut off mid-path and keeps no cause."""
    from planlens.review.host import scrub_paths
    why = ""
    if isinstance(cause, BaseException):
        why = " ".join(scrub_paths(f"{type(cause).__name__}: {cause}").split())
    elif cause:
        why = " ".join(scrub_paths(str(cause)).split())
    if len(why) > 240:
        why = why[:240] + " …"
    n = len(os.path.abspath(path))
    if os.name == "nt" and n >= WINDOWS_MAX_PATH - 1:
        why = (f"the server path is {n} characters long, over Windows' "
               f"{WINDOWS_MAX_PATH}-character limit"
               + (f" ({why})" if why else ""))
    return MarkedCopyNotWritten(
        "could not write the marked copy on the server"
        + (f": {why}" if why else ""))


def _probe_writable(tmp: str) -> None:
    """Create and remove ``tmp`` before planlens writes there: a path the
    server cannot write fails HERE, with its cause, instead of deep inside
    MuPDF with a message cut off mid-path. Raises
    :class:`MarkedCopyNotWritten`."""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(tmp)), exist_ok=True)
        with open(tmp, "wb"):
            pass
        os.remove(tmp)
    except OSError as exc:
        raise _write_failure(tmp, exc) from exc


def _is_write_failure(error_text: str, tmp: str) -> bool:
    """Whether planlens' error is the temporary copy failing to save (a
    MuPDF system error, or one naming the temporary file) rather than a
    problem with the marks."""
    text = str(error_text or "")
    return ("FzErrorSystem" in text or os.path.basename(tmp) in text
            or "cannot open file" in text or "cannot remove file" in text)


def _replace_into(tmp: str, final: str):
    """``os.replace(tmp, final)`` once nothing here holds ``final`` open;
    ``(path written, note or None)``. When ``final`` is still in use
    elsewhere (open in the user's viewer on a Windows host) the copy is
    saved under the next free name instead, and the note says so."""
    import time as _t
    last = None
    for attempt in range(3):
        _conv.forget_path(final)
        try:
            os.replace(tmp, final)
            return final, None
        except PermissionError as exc:
            last = exc
            _t.sleep(0.2 * (attempt + 1))
    stem, ext = os.path.splitext(final)
    n = 2
    while os.path.exists(f"{stem}_{n}{ext}"):
        n += 1
    alt = f"{stem}_{n}{ext}"
    os.replace(tmp, alt)
    return alt, (f"'{os.path.basename(final)}' is in use elsewhere "
                 f"({type(last).__name__ if last else 'locked'}), so the "
                 f"marked copy was saved as '{os.path.basename(alt)}'; tell "
                 "the user which file to open")


def _ours(author: Optional[str], signer: str) -> bool:
    """Whether a markup was written by this host (signed by it, or carrying
    one of the host's ``own_mark_words``), so it may be removed; another
    reviewer's markup in the copy is left alone."""
    a = str(author or "")
    return bool(a) and (a == signer or any(
        w and w in a for w in _host().own_mark_words))


def _is_label(doc, xref) -> bool:
    """Whether an annotation is a label grouped with another (``/RT
    /Group``), as planlens writes a mark's visible label."""
    try:
        kind, val = doc.xref_get_key(int(xref), "RT")
    except Exception:  # noqa: BLE001
        return False
    return kind == "name" and str(val).lstrip("/") == "Group"


#: The keys a ``remove`` entry written as an object may hold.
REMOVE_ENTRY_KEYS = ("id", "text")

#: Words a ``remove`` object's key may be meant as, for the did-you-mean.
_REMOVE_KEY_HINTS = {
    "markupid": ("id",), "markup": ("id",), "mark": ("id",),
    "markid": ("id",), "annotationid": ("id",),
    "comment": ("text",), "label": ("text",), "words": ("text",),
    "says": ("text",), "quote": ("text",),
}


def remove_problem(remove) -> Optional[Dict[str, Any]]:
    """The refusal for a ``remove`` entry written as an object with a key
    other than ``id`` / ``text``, or ``None``. Such an entry used to be
    skipped without a word (``remove=[{"markup_id": ...}]`` removed nothing
    and said nothing; BACKLOG DR-32), so it is refused, naming the valid
    keys and the one most likely meant, before anything is written."""
    from planlens.review.host import check_nested_keys
    entries = ([remove] if isinstance(remove, (str, dict))
               else list(remove or []))
    for i, raw in enumerate(entries):
        if not isinstance(raw, dict):
            continue
        try:
            check_nested_keys(raw, REMOVE_ENTRY_KEYS,
                              method="annotate_document",
                              label=f"remove[{i}]", hints=_REMOVE_KEY_HINTS)
        except ValueError as exc:
            return {"error": str(exc), "hint": (
                "a remove entry is a markup id as document_markups lists it "
                "('p0.m1') or words from its comment or label, given as a "
                "string (or as {\"id\": ...} / {\"text\": ...}); nothing "
                "was removed or written")}
    return None


def remove_markups(pdf: bytes, remove, signer: str):
    """``(new_pdf_bytes, removed, not_removed)``: the marks named in
    ``remove`` taken out of a marked copy, each with its label and any
    reply to it. An entry is a markup id as document_markups lists it
    (``"p0.m1"``) or words from its comment, which must name exactly one
    mark this app wrote. Only this app's marks are removed."""
    import pymupdf
    from planlens.document.annotations import extract_annotations

    doc = pymupdf.open(stream=bytes(pdf), filetype="pdf")
    try:
        per_page = {i: extract_annotations(doc[i], i)[0]
                    for i in range(doc.page_count)}
        everything = [m for ms in per_page.values() for m in ms]
        chosen, removed, refused = {}, [], []
        for raw in ([remove] if isinstance(remove, (str, dict))
                    else list(remove or [])):
            want = (raw.get("id") or raw.get("text") or "") \
                if isinstance(raw, dict) else str(raw or "")
            want = want.strip()
            if not want:
                continue
            m_id = _MARKUP_ID.match(want)
            if m_id:
                hits = [m for m in per_page.get(int(m_id.group(1)), [])
                        if m.id == want]
                if not hits:
                    refused.append({"remove": want, "reason": (
                        "no markup has this id in the copy now (ids are "
                        "positions on a page and change after a removal: "
                        "list them again with document_markups)")})
                    continue
            else:
                # A mark's label rides on it (removed with it), so only the
                # marks themselves are matched by their words.
                low = want.lower()
                hits = [m for m in everything if _ours(m.author, signer)
                        and not _is_label(doc, m.xref)
                        and low in (m.text or "").lower()]
                if len(hits) != 1:
                    refused.append({"remove": want, "reason": (
                        "no mark this app wrote says that" if not hits else
                        f"{len(hits)} marks say that "
                        f"({', '.join(m.id for m in hits[:8])}): give the "
                        "id of the one meant")})
                    continue
            m = hits[0]
            if not _ours(m.author, signer):
                refused.append({"remove": want, "reason": (
                    f"{m.id} is {m.author or 'another reviewer'}'s markup, "
                    "not one this app wrote; it is left as it is")})
                continue
            chosen[(m.page, m.xref)] = m
        for (page_no, xref), m in chosen.items():
            page = doc[page_no]
            doomed = {xref}
            grew = True
            while grew:                 # its label, and replies to either
                grew = False
                for a in page.annots() or []:
                    if a.xref in doomed:
                        continue
                    kind, val = doc.xref_get_key(a.xref, "IRT")
                    if kind == "xref" and val:
                        try:
                            parent = int(str(val).split()[0])
                        except ValueError:
                            continue
                        if parent in doomed:
                            doomed.add(a.xref)
                            grew = True
            for x in sorted(doomed, reverse=True):
                try:
                    page.delete_annot(page.load_annot(x))
                except Exception:  # noqa: BLE001 - already gone with a parent
                    pass
            removed.append({"id": m.id, "page": m.page, "pdf_page": m.page + 1,
                            "kind": m.kind, "says": (m.text or "")[:120],
                            "with_attached": len(doomed) - 1})
        return doc.tobytes(garbage=1), removed, refused
    finally:
        doc.close()


def _handle_path(handle: str) -> Optional[str]:
    """The file a document handle (or a file name given as one) stands for
    in this conversation, when it is a file; ``None`` otherwise."""
    try:
        if _conv.looks_like_handle(handle):
            entry = _conv.document_entry(handle)
            if getattr(entry, "path", None):
                return str(entry.path)
            handle = _conv.handle_source(handle) or ""
        if not handle:
            return None
        return _host().readable(handle)
    except Exception:  # noqa: BLE001 - not a file, then
        return None


def write_marked_copy(handle: str, markups: Optional[list], output_path: str,
                      append: bool, author: str, call, remove=None,
                      image=None) -> Dict[str, Any]:
    """Write ``annotate_document``'s marked copy safely (see the section
    comment): to a temporary file, then swapped in. ``call(args) -> str`` runs
    planlens' annotate_document (the host's dispatch). ``remove`` takes marks
    out of an existing marked copy first, so ``remove`` + ``markups``
    replaces one mark and keeps the rest. Returns the result dict, its
    ``output_path`` the final file's absolute path.

    ``image`` is ``(bytes, name)`` when the document is an IMAGE file
    (:func:`image_of`): the marks' positions are then in its own pixels, a
    new copy's page is the image at one point a pixel, and the result
    carries a private ``_image_frame`` that :func:`image_frame_result`
    turns into its ``frame`` note once the check has read the copy. A mark
    centred off the image -- or, for a PDF, off its page -- is refused
    before anything is written (BACKLOG VIS-26)."""
    try:
        result = _write_marked_copy(handle, markups, output_path, append,
                                    author, call, remove, image)
    except MarkedCopyNotWritten as exc:
        return {"error": str(exc), "hint": WRITE_FAILED_HINT}
    # The host's last word on the result (the app checks the documents the
    # notes cite against what the conversation looked up): a line in the
    # result, the copy written either way.
    after = _host().after_marked_copy
    if after is None:
        return result
    try:
        return after(result, list(markups or []))
    except Exception:  # noqa: BLE001 - never the marked copy's result
        return result


def markups_with_pages(markups) -> tuple:
    """``(markups, None)`` with each markup's ``pdf_page`` (1-based, the
    number a viewer shows) turned into planlens' 0-based ``page``, or
    ``([], error)``: a markup whose ``page`` and ``pdf_page`` name
    different pages is refused, never guessed (live smoke wave 2c)."""
    from planlens.review.page_numbers import resolve_page
    out = []
    for i, m in enumerate(list(markups or [])):
        if isinstance(m, dict) and "pdf_page" in m:
            m = dict(m)
            page, problem = resolve_page(m.get("page"), m.pop("pdf_page"),
                                         default=None)
            if problem is not None:
                return [], {"error": f"markup {i}: {problem['error']}",
                            "hint": problem.get("hint", "")}
            m["page"] = page
        out.append(m)
    return out, None


#: Said for a markup that named its own author (BACKLOG DR-32).
AUTHOR_IGNORED_NOTE = ("'author' is not a markup field in this app and was "
                       "ignored: the app signs every comment itself")


def markups_signed_by_app(markups) -> tuple:
    """``(markups, adjusted)``: each markup without an ``author`` of its
    own, and a row ``{"index", "notes"}`` (planlens' ``adjusted`` shape)
    for each one that had one. planlens lets a markup's ``author`` replace
    the signature for that mark, and one recorded call signed 28 marks "AI
    draft review" that way; in this app the signature is always the app's
    (brief 5), so the field is dropped with a note rather than refused."""
    out, adjusted = [], []
    for i, m in enumerate(list(markups or [])):
        if isinstance(m, dict) and "author" in m:
            m = {k: v for k, v in m.items() if k != "author"}
            adjusted.append({"index": i, "notes": [AUTHOR_IGNORED_NOTE]})
        out.append(m)
    return out, adjusted


def _merge_adjusted(result: Dict[str, Any], rows) -> None:
    """Add ``rows`` (``{"index", "notes"}``) to ``result["adjusted"]``,
    beside planlens' own notes for the same markup."""
    if not rows:
        return
    have = [r for r in (result.get("adjusted") or ()) if isinstance(r, dict)]
    by_index = {r.get("index"): r for r in have}
    for row in rows:
        old = by_index.get(row["index"])
        if old is None:
            have.append(row)
        else:
            old["notes"] = list(old.get("notes") or ()) + row["notes"]
    result["adjusted"] = sorted(have, key=lambda r: (
        (0, r["index"], "") if isinstance(r.get("index"), int)
        else (1, 0, str(r.get("index")))))


def empty_comments_note(markups, result: Dict[str, Any]) -> Optional[str]:
    """A line for a result whose WRITTEN marks include some with no
    comment, or ``None``. planlens' spec says the comment is required but
    writes an empty note without one; seven circle marks of one Foundry
    task went out that way (BACKLOG DR-32). They are said, not refused."""
    skipped = {r.get("index") for r in (result.get("skipped") or ())
               if isinstance(r, dict)}
    empty = [i for i, m in enumerate(list(markups or []))
             if isinstance(m, dict) and i not in skipped
             and not str(m.get("comment") or "").strip()]
    if not empty:
        return None
    which = ", ".join(str(i) for i in empty[:12]) + (
        ", ..." if len(empty) > 12 else "")
    said = (f"markup {which} has no comment, so it was" if len(empty) == 1
            else f"markups {which} have no comment, so each was")
    note = (f"{said} written as a mark with an empty note. Give each its "
            "comment: remove=[its id] and the mark again with one, or write "
            "the copy again with append=false.")
    if "skipped_truncated_after" in result:
        # planlens cut its skipped list to fit: a mark named here may be
        # one of the skipped rows it left out.
        note += (" The skipped list was cut short, so some of these may "
                 "have been skipped rather than written.")
    return note


# --- marks on an image, and marks off the page (BACKLOG VIS-26) -------------
#
# Live wave w4 (2026-10-14, F39): on a phone photo the first ring was drawn
# off the page twice. Every look at an image is in its own pixels (VIS-09),
# but planlens opens an image as a page in points at the resolution the file
# declares (0.75 pt a pixel with none), so the ring went to three quarters
# of x -- past the right edge. Now a mark on an image is taken in the
# image's pixels and put on a copy whose page is the image at one point a
# pixel (``image_view.as_pdf``), converted exactly to whatever copy it goes
# on; and a mark whose centre is off the image (or off a PDF's page) is
# refused, naming the size, before anything is written.

#: Fields of a markup that give a position, by shape (a nested ``anchor``
#: object may hold them too, as planlens reads it).
_BOX_FIELDS = ("bbox", "box", "page_bbox", "view")
_POINT_FIELDS = ("point", "points_at")


def image_of(handle: str, attachments: Optional[Dict[str, bytes]] = None):
    """``(bytes, name)`` of the IMAGE file a document handle -- or a file
    name or attachment key given as one -- was opened from; ``None`` for a
    PDF, or when its source cannot be read."""
    src = str(handle or "").strip()
    if _conv.looks_like_handle(src):
        src = _conv.handle_source(src) or ""
    if not src:
        return None
    try:
        resolved = _conv.resolve_document_source(src, attachments)
    except Exception:  # noqa: BLE001 - not a source: planlens says so itself
        return None
    from planlens.review.host import image_kind as _image_kind
    if isinstance(resolved, (bytes, bytearray)):
        data = bytes(resolved)
        if _image_kind(data[:16]) is None:
            return None
    else:
        try:
            with open(resolved, "rb") as fh:
                if _image_kind(fh.read(16)) is None:
                    return None
                fh.seek(0)
                data = fh.read()
        except OSError:
            return None
    return data, os.path.basename(str(src).replace("\\", "/"))


def _nums(value, n: int):
    try:
        out = [float(v) for v in value]
    except (TypeError, ValueError):
        return None
    return out if len(out) == n else None


def mark_position(markup: Dict[str, Any]):
    """The box a markup is placed by, in the frame it was given in --
    ``view`` + ``image_box``, a box, a point (as a zero-size box) or a
    callout's ``points_at`` -- or ``None`` (a quote, a reply, or values that
    are not numbers: planlens says what is wrong with those)."""
    if not isinstance(markup, dict):
        return None
    m = dict(markup)
    if isinstance(m.get("anchor"), dict):
        m = {**m["anchor"], **{k: v for k, v in m.items() if k != "anchor"}}
    if str(m.get("kind") or "").lower() == "callout" and \
            _nums(m.get("points_at"), 2):
        x, y = _nums(m["points_at"], 2)
        return [x, y, x, y]
    view, image_box = _nums(m.get("view"), 4), _nums(m.get("image_box"), 4)
    if view and image_box:
        from planlens.review.frames import image_box_to_page
        try:
            return list(image_box_to_page(view, image_box))
        except ValueError:
            return None
    for name in ("bbox", "box", "page_bbox"):
        box = _nums(m.get(name), 4)
        if box:
            return box
    for name in _POINT_FIELDS:
        pt = _nums(m.get(name), 2)
        if pt:
            return [pt[0], pt[1], pt[0], pt[1]]
    return None


def marks_off_the_page(markups, page_size, unit: str, what: str
                       ) -> Optional[Dict[str, Any]]:
    """The refusal for marks whose position (:func:`mark_position`) is
    centred off their page, or ``None``. ``page_size(page) -> (w, h)`` in
    the frame the marks are given in (``None`` for a page it cannot say)."""
    from planlens.review.host import off_image_note
    bad = []
    for i, m in enumerate(list(markups or [])):
        where = mark_position(m)
        if where is None:
            continue
        try:
            page = int(m.get("page", 0))
        except (TypeError, ValueError):
            continue
        size = page_size(page)
        if not size:
            continue
        why = off_image_note(where, size[0], size[1], unit, what)
        if why:
            kind = m.get("kind") or "mark"
            bad.append(f"markup {i} ({kind}"
                       + (f", page {page}" if what == "page" else "")
                       + f") is {why}")
    if not bad:
        return None
    if what == "image":
        hint = ("on an image, bbox, point and view are in its own pixels "
                "(top-left origin, y down), as analyze_image and "
                "render_region on it give them; nothing was written")
    else:
        hint = ("positions are PDF points on the page as displayed "
                "(top-left origin, y down), as read_document and a look's "
                "view + image_box give them; nothing was written")
    return {"error": f"{len(bad)} mark(s) would fall off the {what}: "
                     + "; ".join(bad[:10]), "hint": hint}


def _scaled_markup(markup, sx: float, sy: float):
    """``markup`` with every position (a nested ``anchor`` too) multiplied
    by ``sx`` across and ``sy`` down; ``image_box`` stays as it is (a 0-999
    box on its view). Values that are not numbers are left for planlens to
    refuse."""
    if not isinstance(markup, dict) or (sx == 1.0 and sy == 1.0):
        return markup

    def scale(d):
        out = dict(d)
        for name in _BOX_FIELDS:
            box = _nums(out.get(name), 4)
            if box:
                out[name] = [box[0] * sx, box[1] * sy, box[2] * sx,
                             box[3] * sy]
        for name in _POINT_FIELDS:
            pt = _nums(out.get(name), 2)
            if pt:
                out[name] = [pt[0] * sx, pt[1] * sy]
        return out

    out = scale(markup)
    if isinstance(out.get("anchor"), dict):
        out["anchor"] = scale(out["anchor"])
    return out


def _page_size_pt(pdf_path: str):
    """``(w, h)`` of page 0 of the PDF at ``pdf_path`` as displayed."""
    import pymupdf
    with pymupdf.open(pdf_path) as doc:
        rect = doc[0].rect
        return float(rect.width), float(rect.height)


def _pdf_page_sizes(handle: str):
    """``page -> (w, h)`` as displayed, in points, of the document a handle
    stands for (``None`` for a page it does not have, or when it is not
    open and does not open)."""
    entry = None
    try:
        entry = (_conv.document_entry(handle)
                 if _conv.looks_like_handle(handle) else None)
        if entry is None:
            entry = _conv.open_document_entry(handle)
    except Exception:  # noqa: BLE001 - planlens says what is wrong itself
        entry = None
    fz = getattr(getattr(entry, "doc", None), "_doc", None)

    def size(page: int):
        if fz is None:
            return None
        try:
            with entry.lock:
                if not 0 <= page < fz.page_count:
                    return None
                rect = fz[page].rect
                return float(rect.width), float(rect.height)
        except Exception:  # noqa: BLE001 - nothing to say about it
            return None

    return size


#: The result's ``frame`` for marks put on an image, by whether the copy's
#: page is the image at one point a pixel.
IMAGE_FRAME_NOTE = (
    "bbox, point and view were taken in this image's own pixels ({w} x {h} "
    "px, top-left origin, y down), as its looks give them; the marked copy "
    "is a PDF whose page is the image at one point a pixel, so every box on "
    "it -- this result's, the check's, a look at the copy -- is in those "
    "same pixels")
IMAGE_FRAME_SCALED_NOTE = (
    "bbox, point and view were taken in this image's own pixels ({w} x {h} "
    "px, top-left origin, y down), as its looks give them; the marked "
    "copy's page is the image at {s:.4g} pt a pixel, so this result's boxes "
    "are converted back to the image's pixels, but a look at the copy "
    "itself gives its points (the image's pixels x {s:.4g})")


def image_frame_result(result: Dict[str, Any]) -> None:
    """Finish an image's annotate result, in place: its boxes -- the written
    rows' and the check's -- back in the image's own pixels when the copy's
    page is not the image at one point a pixel, and the ``frame`` note.
    Takes the private ``_image_frame`` :func:`write_marked_copy` left."""
    frame = result.pop("_image_frame", None) if isinstance(result, dict) \
        else None
    if not frame:
        return
    (w, h), (sx, sy) = frame["image_px"], frame["scale"]
    same = abs(sx - 1.0) < 1e-6 and abs(sy - 1.0) < 1e-6
    if not same:
        def back(values):
            n = _nums(values, len(values)) if isinstance(
                values, (list, tuple)) else None
            if not n or len(n) not in (2, 4):
                return values
            f = [sx, sy] * (len(n) // 2)
            return [round(v / k, 1) for v, k in zip(n, f)]

        for row in result.get("written") or ():
            if isinstance(row, dict):
                for name in ("bbox", "target", "label_bbox", "points_at"):
                    if row.get(name) is not None:
                        row[name] = back(row[name])
        check = result.get("check")
        for row in (check.get("misplaced") or ()) if isinstance(
                check, dict) else ():
            if isinstance(row, dict):
                for name in ("bbox", "found_bbox"):
                    if row.get(name) is not None:
                        row[name] = back(row[name])
    result["frame"] = (IMAGE_FRAME_NOTE.format(w=w, h=h) if same else
                       IMAGE_FRAME_SCALED_NOTE.format(w=w, h=h, s=sx))


def _nothing_written(result: Dict[str, Any], final: str, existed: bool,
                     had_marks: bool, removed, refused) -> Dict[str, Any]:
    """``result`` for a call that placed no mark and removed none: no
    ``output_path`` (nothing to download), and a note saying nothing was
    written, why, and which copy -- if any -- is still there unchanged."""
    out = {k: v for k, v in result.items()
           if k not in ("output_path", "appended_to_existing", "note")}
    name = os.path.basename(final)
    why = ("every mark was skipped: read each skipped row's reason"
           if had_marks else "no mark named in remove could be removed")
    kept = (f"'{name}' is unchanged, as it was before this call" if existed
            else f"'{name}' was not created")
    out["nothing_written"] = True
    out["note"] = (f"Nothing was written: {why}. No marked copy was saved; "
                   f"{kept}. Fix what the reasons say and call again; do not "
                   "offer the user a marked copy from this call.")
    if removed is not None:
        out["removed"] = removed
    if refused:
        out["not_removed"] = refused
    return out


def _write_marked_copy(handle, markups, output_path, append, author, call,
                       remove, image=None) -> Dict[str, Any]:
    """:func:`write_marked_copy`; a file-system failure raises
    :class:`MarkedCopyNotWritten` (D5)."""
    import shutil
    marks, problem = markups_with_pages(markups)
    if problem is not None:
        return problem
    problem = remove_problem(remove)
    if problem is not None:
        return problem
    # The app signs every mark (brief 5); a markup's own author is dropped.
    marks, unsigned = markups_signed_by_app(marks)
    picture = None
    if image is not None:
        images = _host().images
        picture, refusal = (images.open_image(image[0], image[1])
                            if images is not None else (None, None))
        if refusal is not None:
            return json.loads(refusal)
        if picture is None:
            return {"error": ("this image could not be decoded here (Pillow "
                              "is missing, or the file is damaged), so marks "
                              "cannot be placed in its pixels")}
        off = marks_off_the_page(
            marks, lambda p: ((picture.width, picture.height) if p == 0
                              else None), "px", "image")
    else:
        off = marks_off_the_page(marks, _pdf_page_sizes(handle), "pt",
                                 "page") if marks else None
    if off is not None:
        return off
    final = markup_output_path(output_path, handle)
    existed = os.path.isfile(final)
    tmp = _temp_beside(final)
    try:
        removed = refused = None
        fresh_image = False
        if remove:
            if not existed:
                return {"error": (
                    f"nothing to remove from: '{os.path.basename(final)}' "
                    "does not exist yet"),
                    "hint": ("remove takes marks out of a marked copy "
                             "written earlier; name it in output_path")}
            with open(final, "rb") as fh:
                data = fh.read()
            data, removed, refused = remove_markups(data, remove, author)
            try:
                with open(tmp, "wb") as fh:
                    fh.write(data)
            except OSError as exc:
                raise _write_failure(tmp, exc) from exc
        elif append and existed:
            try:
                shutil.copyfile(final, tmp)
            except OSError as exc:
                raise _write_failure(tmp, exc) from exc
        else:
            _probe_writable(tmp)
            src = _handle_path(handle)
            if src and os.path.normcase(os.path.abspath(src)) == \
                    os.path.normcase(os.path.abspath(final)):
                return {"error": (
                    "this handle is the marked copy itself, and append=false "
                    "would write every mark again on top of the ones it has"),
                    "hint": ("to rebuild the copy, pass the ORIGINAL "
                             "document's handle with append=false; to change "
                             "or delete one mark, pass remove=[its id] (and "
                             "the new mark in markups) with output_path = "
                             "this copy")}
            if picture is not None and marks:
                # A new copy of an image: its page is the image at one point
                # a pixel, written here and marked by planlens as an existing
                # copy (VIS-26), not planlens' own page at the file's dpi.
                try:
                    with open(tmp, "wb") as fh:
                        fh.write(_host().images.as_pdf(picture))
                except OSError as exc:
                    raise _write_failure(tmp, exc) from exc
                fresh_image = True
        frame = None
        if picture is not None and marks:
            # The copy's own page decides the conversion: the new page above,
            # or an existing copy's (made earlier, maybe at another scale).
            try:
                cw, ch = _page_size_pt(tmp)
            except Exception as exc:  # noqa: BLE001 - said, never guessed
                return {"error": (f"the marked copy to add to could not be "
                                  f"read: {type(exc).__name__}: {exc}"),
                        "hint": ("rebuild it from the image with "
                                 "append=false")}
            sx, sy = cw / picture.width, ch / picture.height
            marks = [_scaled_markup(m, sx, sy) for m in marks]
            frame = {"image_px": [picture.width, picture.height],
                     "scale": [sx, sy]}
        if marks:
            raw = call({"handle": handle, "output_path": tmp,
                        "markups": marks, "author": author,
                        # onto the temporary copy when there is one
                        "append": bool(remove or append or fresh_image)})
            try:
                result = json.loads(raw)
            except (TypeError, ValueError):
                return {"error": str(raw)[:500]}
            if isinstance(result, dict) and "error" in result \
                    and _is_write_failure(result["error"], tmp):
                # MuPDF could not save the temporary copy: said as the
                # server-side failure it is, not "try it again once" (D5).
                raise _write_failure(tmp, result["error"])
            if not isinstance(result, dict) or "error" in result:
                return result if isinstance(result, dict) else \
                    {"error": str(result)[:500]}
            from planlens.review.page_numbers import range_hint
            for row in result.get("skipped") or ():
                hint = range_hint(str(row.get("reason") or "")) \
                    if isinstance(row, dict) else None
                if hint:
                    row["hint"] = hint
        elif remove:
            result = {"handle": handle, "author": author, "n_written": 0,
                      "n_skipped": 0}
        else:
            return {"error": "markups is empty: nothing would be written",
                    "hint": ("each markup is {kind, page, comment} plus ONE "
                             "anchor; to take marks out, pass remove")}
        if not int(result.get("n_written") or 0) and not removed:
            # Every mark skipped and nothing taken out: no file is written,
            # so no "marked" copy without marks becomes a download card
            # (live smoke wave 3, F3: F04's first call saved a 0-mark copy).
            return _nothing_written(result, final, existed,
                                    bool(marks), removed, refused)
        try:
            written, moved = _replace_into(tmp, final)
        except OSError as exc:
            raise _write_failure(final, exc) from exc
        result["output_path"] = written
        result["appended_to_existing"] = bool(existed and (append or remove))
        _merge_adjusted(result, unsigned)
        empty = empty_comments_note(marks, result)
        if empty:
            result["empty_comments"] = empty
        if frame is not None:
            result["_image_frame"] = frame
        if moved:
            result["saved_elsewhere"] = moved
        if removed is not None:
            result["removed"] = removed
            if refused:
                result["not_removed"] = refused
            result["ids_note"] = (
                "markup ids are positions on a page: after a removal the "
                "ids on that page change; list them with document_markups "
                "before removing another by id")
        return result
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


__all__ = ["markup_output_path", "forget_path", "WINDOWS_MAX_PATH",
           "MarkedCopyNotWritten", "WRITE_FAILED_HINT", "REMOVE_ENTRY_KEYS",
           "remove_problem", "remove_markups", "write_marked_copy",
           "markups_with_pages", "AUTHOR_IGNORED_NOTE",
           "markups_signed_by_app", "empty_comments_note", "image_of",
           "mark_position", "marks_off_the_page", "IMAGE_FRAME_NOTE",
           "IMAGE_FRAME_SCALED_NOTE", "image_frame_result"]

#: Re-exported: closing a conversation's handles on a file before it is
#: replaced is the toolkit's business (:mod:`planlens.review.conversation`).
forget_path = _conv.forget_path
