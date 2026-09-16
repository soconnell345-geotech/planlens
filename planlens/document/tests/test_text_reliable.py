"""A page can HAVE a text layer and still not say what the page says.

An analysis-program printout bound into a report often carries a font with no
usable Unicode map: the extraction succeeds, returns thousands of characters,
and half of them are U+FFFD. Nothing downstream could tell that apart from
ordinary prose — the page was not `needs_ocr`, it was not a scan, and its
transcript went to the model as if it were text. ``text_reliable`` is the
page map saying so.

Every page here is built, never taken from a document:
:func:`planlens.testing.build_unmapped_text_pdf` draws character codes the
font's own map sends to U+FFFD, at a proportion the caller sets. That
proportion of the EXTRACTED string is the signal the rule reads, so the first
test checks the fixture really does defeat the extractor before any of the
others claim anything about it.
"""

import pytest

fitz = pytest.importorskip("fitz")

from planlens.document import open_document                      # noqa: E402
from planlens.document.advice import page_advice                 # noqa: E402
from planlens.document.azure_di import pages_needing_ocr         # noqa: E402
from planlens.document.pdf_text import MAX_UNMAPPED_FRACTION     # noqa: E402
from planlens.testing import build_unmapped_text_pdf             # noqa: E402

LETTER = (612.0, 792.0)


def test_the_fixture_really_does_defeat_the_extractor():
    """Before anything is claimed about it: the text really is undecodable."""
    with open_document(build_unmapped_text_pdf(0.5)) as doc:
        text = doc.page(0).text()
    assert len(text) > 400
    assert 0.4 < text.count("�") / len(text) < 0.6


def test_a_page_whose_font_maps_to_nothing_is_not_reliable():
    with open_document(build_unmapped_text_pdf(0.5)) as doc:
        s = doc.summary(0)
    assert s.n_text_chars > 400          # the extraction SUCCEEDED
    assert s.text_reliable is False
    assert 0.4 < s.unmapped_fraction < 0.6
    # It joins the pages whose words must be read off the picture.
    assert s.evidence["needs_ocr"] is True
    assert s.evidence["text_unreliable"] is True


def test_a_stray_undecodable_character_does_not_condemn_the_page():
    """Measured: a bullet or a logo glyph costs under 1% of a real page.

    Those pages read perfectly and must not be sent to OCR or flagged.
    """
    with open_document(build_unmapped_text_pdf(0.025)) as doc:
        s = doc.summary(0)
    assert 0 < s.unmapped_fraction < MAX_UNMAPPED_FRACTION
    assert s.text_reliable is True
    assert "needs_ocr" not in s.evidence
    assert "text_unreliable" not in s.evidence
    # ... the count is still reported, because the characters are still gone
    assert s.evidence["unmapped_chars"] > 0


def test_a_page_with_no_text_at_all_is_reliable_by_default():
    """0 of 0 is not a fraction, and a blank page is not a broken one."""
    doc = fitz.open()
    doc.new_page(width=LETTER[0], height=LETTER[1])
    data = doc.tobytes()
    doc.close()
    with open_document(data) as opened:
        s = opened.summary(0)
    assert s.n_text_chars == 0
    assert (s.unmapped_fraction, s.text_reliable) == (0.0, True)


def test_the_threshold_is_the_measured_one():
    """0.10, from a 38x gap between the two populations — see pdf_text.py."""
    assert MAX_UNMAPPED_FRACTION == 0.10
    for fraction, reliable in ((0.05, True), (0.075, True), (0.2, False),
                               (0.5, False), (0.95, False)):
        with open_document(build_unmapped_text_pdf(fraction)) as doc:
            s = doc.summary(0)
        assert s.text_reliable is reliable, (fraction, s.unmapped_fraction)


def test_such_a_page_is_offered_to_ocr():
    with open_document(build_unmapped_text_pdf(0.5)) as doc:
        assert pages_needing_ocr(doc) == [0]
    with open_document(build_unmapped_text_pdf(0.025)) as doc:
        assert pages_needing_ocr(doc) == []


def test_the_advice_tells_the_model_not_to_quote_the_text():
    with open_document(build_unmapped_text_pdf(0.5)) as doc:
        s = doc.summary(0)
        lines = page_advice(s, doc.page(0))
    said = " ".join(lines).lower()
    assert "unreliable" in said
    assert "view the page" in said
    # The mild "N characters could not be decoded" note is REPLACED, not
    # added to: two statements about one problem read as two problems.
    assert sum(1 for ln in lines if "u+fffd" in ln.lower()) == 1


def test_the_page_map_row_carries_the_warning():
    with open_document(build_unmapped_text_pdf(0.5)) as doc:
        row = doc.summary(0).to_dict(detail=False)
    assert row["text_unreliable"] is True
    assert 0.4 < row["unmapped_fraction"] < 0.6
    with open_document(build_unmapped_text_pdf(0.025)) as doc:
        clean = doc.summary(0).to_dict(detail=False)
    # A row that says nothing is a row with ordinary text.
    assert "text_unreliable" not in clean and "unmapped_fraction" not in clean
