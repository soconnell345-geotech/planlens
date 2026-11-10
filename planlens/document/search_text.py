"""The text an exact search compares: one folding for the page and the query.

A page's text is kept as the PDF holds it: a ligature as one character
(U+FB01 for "fi"), every space the typesetter set, the no-break space, a word
hyphenated at the end of a line. None of that is what a reader sees or types,
so an exact search for "specified" missed a page that prints it (review
2026-11-10). :meth:`Document.search` therefore compares FOLDED text on both
sides and maps each match back to the page's own characters: a hit still
quotes and locates the text as the page has it.

Folded text is each character's Unicode compatibility decomposition (NFKD: a
ligature becomes its letters, a no-break space a space, a superscript its
digit) with every run of white space as one space. A hyphen that ends a line
is the word's own (load-/bearing) or only the line break's (founda-/tion),
and the page does not say which, so a block with one is read each way
(:func:`readings`).
"""

from __future__ import annotations

import re
import unicodedata
from typing import (
    Collection, Iterator, List, NamedTuple, Optional, Sequence, Tuple,
)

#: The characters that end a line where a word is broken.
LINE_HYPHENS = "-‐­"

#: White space that folding changes: a run of it, or one that is not a space.
_UNFOLDED_SPACE = re.compile(r"\s\s+|[^\S ]")

#: What folding may change at all: that white space, or a run of characters
#: outside ASCII (most of which decompose to themselves).
_TO_FOLD = re.compile(r"\s\s+|[^\S ]|[^\x00-\x7f\s]+")


class Folded(NamedTuple):
    """Folded text and, for each of its characters and for its end, the
    index of the original character it came from (``None``: the text was
    already folded, so the two are the same)."""
    text: str
    at: Optional[List[int]]

    def span(self, start: int, end: int) -> Tuple[int, int]:
        """The original characters a folded match ``[start, end)`` covers."""
        if self.at is None:
            return start, end
        if end <= start:
            return self.at[start], self.at[start]
        return self.at[start], self.at[end - 1] + 1

    def without(self, skip: Collection[int]) -> "Folded":
        """This text with the original characters at ``skip`` left out."""
        at = self.at if self.at is not None else range(len(self.text) + 1)
        keep = [i for i in range(len(self.text)) if at[i] not in skip]
        return Folded("".join(self.text[i] for i in keep),
                      [at[i] for i in keep] + [at[len(self.text)]])


def fold(text: str) -> Folded:
    """``text`` folded for matching (see the module)."""
    if unicodedata.is_normalized("NFKD", text) \
            and not _UNFOLDED_SPACE.search(text):
        return Folded(text, None)
    out: List[str] = []
    at: List[int] = []
    pos, spaced = 0, False
    for m in _TO_FOLD.finditer(text):
        if m.start() > pos:                 # as it is: nothing to fold in it
            out.append(text[pos:m.start()])
            at.extend(range(pos, m.start()))
            spaced = text[m.start() - 1] == " "
        for i in range(m.start(), m.end()):
            for c in unicodedata.normalize("NFKD", text[i]):
                if c.isspace():
                    if spaced:
                        continue
                    c = " "
                out.append(c)
                at.append(i)
                spaced = c == " "
        pos = m.end()
    out.append(text[pos:])
    at.extend(range(pos, len(text) + 1))
    return Folded("".join(out), at)


def readings(text: str, line_ends: Sequence[int]) -> List[Folded]:
    """The ways ``text`` (lines joined by one character each) is read for
    matching. ``line_ends`` are the indices of the joining characters.

    Always the text as joined. Where a line ends in a hyphen straight after
    a letter or digit and the next line begins with one, also the text with
    each such break closed up ("load-bearing"), and, where letters meet
    across it, with the hyphen gone as well ("foundation").
    """
    base = fold(text)
    gaps: set = set()         # the white space round each such break
    hyphens: set = set()      # the hyphens of those between two letters
    for end in line_ends:
        a, b = end, end + 1
        while a > 0 and text[a - 1].isspace():
            a -= 1
        while b < len(text) and text[b].isspace():
            b += 1
        if a < 2 or b >= len(text) or text[a - 1] not in LINE_HYPHENS:
            continue
        before, after = text[a - 2], text[b]
        if not (before.isalnum() and after.isalnum()):
            continue
        gaps.update(range(a, b))
        if before.isalpha() and after.isalpha():
            hyphens.add(a - 1)
    if not gaps:
        return [base]
    out = [base, base.without(gaps)]
    if hyphens:
        out.append(base.without(gaps | hyphens))
    return out


def find_spans(rx: "re.Pattern[str]", forms: Sequence[Folded]
               ) -> Iterator[Tuple[int, int]]:
    """The original ``(start, end)`` of every match of ``rx`` in any of
    ``forms`` (one text's :func:`readings`), each once, in reading order."""
    if len(forms) == 1:
        for m in rx.finditer(forms[0].text):
            yield forms[0].span(m.start(), m.end())
        return
    found = set()
    for form in forms:
        for m in rx.finditer(form.text):
            found.add(form.span(m.start(), m.end()))
    yield from sorted(found)
