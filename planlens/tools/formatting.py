"""Rendering and budgeting helpers for the tool layer.

Everything a tool returns is read by a language model through a harness that
usually truncates long results — the reference host cuts tool output at 8,000
characters, mid-JSON. So every response is built to a character budget, and
anything that does not fit is continued through an explicit cursor rather than
cut off.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from planlens.document.model import (
    SOURCE_AZURE_DI, SOURCE_CAD_HIDDEN, Markup, PageContent, PageSummary,
    TextLine,
)


def compact_ranges(pages: Iterable[int]) -> str:
    """``[0, 1, 2, 5, 7, 8]`` -> ``"0-2,5,7-8"`` (0-based, as given)."""
    nums = sorted(set(int(p) for p in pages))
    if not nums:
        return ""
    parts, start, prev = [], nums[0], nums[0]
    for n in nums[1:] + [None]:
        if n is not None and n == prev + 1:
            prev = n
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        if n is not None:
            start = prev = n
    return ",".join(parts)


def json_len(obj: Any) -> int:
    return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


def fit_items(items: Sequence[Any], budget: int,
              to_payload: Callable[[Any], Any] = lambda x: x,
              start: int = 0) -> Tuple[List[Any], Optional[int]]:
    """As many payloads from ``items[start:]`` as fit in ``budget`` characters.

    Returns ``(payloads, next_index)``; ``next_index`` is None when everything
    fitted. At least one item is always returned when any remain, so a caller
    paging through can never stall — an item larger than the whole budget is
    the caller's to shrink.
    """
    out: List[Any] = []
    used = 2
    for i in range(start, len(items)):
        p = to_payload(items[i])
        size = json_len(p) + 1
        if out and used + size > budget:
            return out, i
        out.append(p)
        used += size
    return out, None


def _box(b: Sequence[float]) -> str:
    return ",".join(str(int(round(v))) for v in b)


def render_line(ln: TextLine, with_locations: bool) -> str:
    if not with_locations:
        return ln.text
    tags = [f"{ln.id} @ {_box(ln.bbox)}"]
    if ln.rotation is None:
        tags.append("dir ?")
    elif ln.rotation:
        tags.append(f"r{ln.rotation:g}")
    if ln.source == SOURCE_CAD_HIDDEN:
        tags.append("cad")
    elif ln.source == SOURCE_AZURE_DI:
        tags.append("azure")
    if ln.confidence < 1.0:
        tags.append(f"conf {ln.confidence:.2f}")
    return f"[{' '.join(tags)}] {ln.text}"


def render_markup(m: Markup, with_locations: bool = True) -> str:
    what = m.kind + (f"/{m.intent}" if m.intent and m.intent != m.kind else "")
    head = f"[{m.id}] {what}"
    if m.subject:
        head += f' "{m.subject}"'
    if m.author:
        head += f" by {m.author}"
    if m.created:
        head += f", {m.created[:10]}"
    parts = [head]
    if m.text:
        parts.append(f'says: "{" ".join(m.text.split())}"')
    if m.appearance_text:
        parts.append(f'shows: "{m.appearance_text}"')
    if with_locations:
        parts.append(f"box {_box(m.bbox)}")
    if m.points_at is not None:
        parts.append(f"points at {_box(m.points_at)}")
    if m.points_to_markup:
        parts.append(f"aimed at {m.points_to_markup}")
    if m.in_reply_to:
        parts.append(f"reply-linked to {m.in_reply_to}")
    if m.replies:
        parts.append(f"linked replies {','.join(m.replies)}")
    if m.measure is not None:
        parts.append(render_measure(m.measure))
    return " | ".join(parts)


def render_measure(mm) -> str:
    """A measurement markup's calibration, stated value and derived value.

    Both values are shown. Agreement is the evidence that the stored factors
    were read correctly, and a disagreement is a fact the reviewer needs, not
    something to resolve silently by preferring one of them.
    """
    bits = [f"measured at {mm.scale.label}"]
    if mm.stated is not None:
        bits.append(f"states {mm.stated}")
    elif mm.stated_text:
        bits.append(f'says "{mm.stated_text}"')
    if mm.derived is not None:
        bits.append(f"path measures {mm.derived}")
    if mm.derived_area is not None:
        bits.append(f"area {mm.derived_area}")
    warnings = list(mm.warnings) + list(mm.scale.warnings)
    if warnings:
        bits.append("! " + "; ".join(warnings))
    return " ".join(bits)


def render_quantity(q, with_locations: bool = True) -> str:
    """One stated quantity as a line: what it says, where it says it.

    The RAW text is quoted alongside the parsed value on purpose. "40-foot"
    and "approximately 40 ft" are the same number with different authority,
    and a reviewer comparing the narrative against the plan is reading that
    difference as much as the number.
    """
    value = f"{q.value:g}"
    if q.value_to is not None:
        value += f" to {q.value_to:g}"
    if q.units:
        value += f" {q.units}"
    parts = [f"p{q.page} {value} [{q.kind}]"]
    if q.qualifier:
        parts.append(q.qualifier)
    parts.append(f'"{" ".join(q.text.split())}"')
    if with_locations and q.bbox is not None:
        parts.append(f"@ {_box(q.bbox)}")
    if q.markup_id:
        # "in markup p1.m1" already says a reviewer wrote it; a second
        # "markup" tag after it is noise on a token budget.
        parts.append(f"in markup {q.markup_id}")
    else:
        if q.line_ids:
            parts.append("(" + ",".join(q.line_ids[:3]) + ")")
        if q.source == SOURCE_CAD_HIDDEN:
            parts.append("cad")
        elif q.source == SOURCE_AZURE_DI:
            parts.append("azure")
        elif q.source != "pdf_text":
            parts.append(q.source)
    return " ".join(parts)


def page_header(s: PageSummary) -> str:
    label = f" ({s.label})" if s.label else ""
    return f"=== page {s.page}{label} [{s.kind}] ==="


def render_page(pc: PageContent, summary: PageSummary, *,
                include_tables: bool, include_markups: bool,
                with_locations: bool,
                advice: Sequence[str] = ()) -> List[str]:
    """The page as a list of output lines (header first)."""
    rows = [page_header(summary)]
    for w in pc.warnings:
        rows.append(f"! {w}")
    for a in advice:
        rows.append(f"! look: {a}")
    body = [render_line(ln, with_locations) for ln in pc.lines]
    rows.extend(body if body else ["[no text on this page]"])
    if include_tables:
        for t in pc.tables:
            rows.append(f"--- table {t.id} ({t.n_rows} rows x {t.n_cols} cols"
                        + (f", box {_box(t.bbox)}" if with_locations else "")
                        + ") ---")
            rows.extend(t.to_markdown().splitlines())
            rows.extend(f"note: {n}" for n in t.notes)
    if include_markups and pc.markups:
        rows.append(f"--- {len(pc.markups)} review markup(s) ---")
        rows.extend(render_markup(m, with_locations) for m in pc.markups)
    return rows
