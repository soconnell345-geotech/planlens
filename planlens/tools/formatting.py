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
    return " | ".join(parts)


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
