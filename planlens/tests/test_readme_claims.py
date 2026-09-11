"""README claims that must stay true as the code moves.

On this package the differentiator IS that the published numbers are true, so
a stale README is a defect rather than a nit. Two classes of claim are cheap
to pin mechanically and were both found stale in the 2026-09-07 close-out
review:

* the **confidence ladder**. ``README.md`` tells a caller that lowering
  ``min_confidence`` to 0.45 still hides the contradicted tier at 0.25. That
  advice is only actionable while those two numbers are the ones the code
  actually caps at — and a caller who trusts a stale ladder silently loses
  proposals rather than seeing an error.
* every **corpus count** must carry the code state it was measured on. Counts
  moved between the committed tip and the close-out tree precisely because
  deletions became caps, so a bare number in prose is not a fact.

Deliberately narrow: this asserts the README quotes the live constants, NOT
that any particular measured count is still reproducible — re-measuring the
corpus belongs to ``score_compositions.py``, which owns the ledger.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from planlens.ir.queries import _CONTRADICTED_CAP, _UNCORROBORATED_CAP

README = Path(__file__).resolve().parents[2] / "README.md"

pytestmark = pytest.mark.skipif(
    not README.is_file(),
    reason="installed (non-source) tree: no README.md to read")


def _readme() -> str:
    return README.read_text(encoding="utf-8")


class TestConfidenceLadderIsDocumented:
    """The three stops a caller picks between must appear, as numbers."""

    def test_documents_both_cap_values(self):
        text = _readme()
        for name, value in (("contradicted", _CONTRADICTED_CAP),
                            ("uncorroborated", _UNCORROBORATED_CAP)):
            assert f"{value:g}" in text, (
                f"README.md does not mention the {name} cap {value:g}; a "
                "caller lowering min_confidence by the documented ladder "
                "would silently lose that tier")

    def test_ladder_is_ordered_as_documented(self):
        # The prose asserts contradicted < uncorroborated < the 0.5 default
        # call threshold. If the constants ever cross, the advice inverts.
        assert _CONTRADICTED_CAP < _UNCORROBORATED_CAP < 0.5


class TestCorpusCountsCarryTheirProvenance:
    """A count without a code state is not a measurement."""

    def test_the_measured_revision_is_named(self):
        # The Phase-3.2 figures were taken at this tip; the close-out
        # figures say 2026-09-07. Both markers must survive edits.
        text = _readme()
        assert "1f6551c" in text
        assert "2026-09-07" in text

    def test_the_ledger_of_record_is_pointed_at(self):
        assert "score_compositions.py" in _readme()


# ---------------------------------------------------------------------------
# The corpus figures the README PUBLISHES
# ---------------------------------------------------------------------------

#: Measured 2026-09-07 by
#: GeotechStaffEngineer/module_work/drawing_ground_truth/doc_claims_check.py
#: — the committed command that regenerates every number below. These are
#: pinned rather than merely present because published figures drifted
#: TWICE during the Phase-3.2 remediation: prose was edited without a run,
#: and nothing failed. Changing a number here without re-running that
#: script is the defect this guard exists to catch.
#:
#: 2026-09-10 (round-5 repair, finding 1): the tipless family was split
#: into "oriented" (admitted) and "blunt" (capped); one 11.01
#: construct's second end re-labelled as oriented and the figure read
#: 18 / 16 / 0. 2026-09-11 (round 6): the oriented rank now also
#: requires arrow scale and a taper, every corpus member of the looser
#: class was a sub-scale glyph fragment, so the oriented class is EMPTY
#: on the corpus and the blunt figure is back at 19 / 17 / 0 —
#: re-measured by doc_claims_check.py (which now prints the oriented
#: row too), not edited.
BLUNT_TOTAL_AT_ZERO = 19
BLUNT_TOTAL_AT_OBSERVATIONAL = 17
BLUNT_PER_SHEET = {"21.01": (17, 15, 0), "11.01": (2, 2, 0)}
ORIENTED_TOTALS = (0, 0, 0)
REHOMED = {"10.17a": 557, "11.01": 158, "5003": 1696}


class TestPublishedCorpusFiguresArePinned:
    """A number in the README must match the script that produced it."""

    def test_blunt_totals_are_the_measured_ones(self):
        text = _readme()
        for value in (BLUNT_TOTAL_AT_ZERO, BLUNT_TOTAL_AT_OBSERVATIONAL):
            assert f"**{value}**" in text, (
                f"README no longer publishes the measured blunt total "
                f"{value}; re-run doc_claims_check.py before editing it")

    def test_blunt_per_sheet_contributions_are_the_measured_ones(self):
        text = _readme()
        for sheet, (a, b, c) in BLUNT_PER_SHEET.items():
            assert f"{sheet} contributes {a} / {b} / {c}" in text, (
                f"README's per-sheet blunt figure for {sheet} is not the "
                f"measured {a} / {b} / {c}")

    def test_the_oriented_figure_is_published_with_its_command(self):
        # The oriented class has no corpus member; the README must say
        # so as a measured figure, in the form doc_claims_check.py
        # prints it, not as prose nobody can re-run.
        a, b, c = ORIENTED_TOTALS
        assert f"oriented-terminator proposals: {a} / {b} / {c}" in _readme()

    def test_the_branch_is_still_documented_as_never_called(self):
        # The whole point of the figure: it reaches the observational
        # band on real drafting and never the 0.5 call threshold.
        assert all(row[2] == 0 for row in BLUNT_PER_SHEET.values())
        assert "none at the 0.5 default" in _readme()

    def test_layer_rehoming_counts_are_the_measured_ones(self):
        text = _readme()
        for sheet, n in REHOMED.items():
            assert f"**{n}**" in text, (
                f"README no longer publishes the measured re-homed count "
                f"{n} for {sheet}")

    def test_the_two_layer_quantities_are_not_conflated(self):
        # The inheritance moves the layers CARRYING GEOMETRY; the
        # n_layers METADATA field counts something else and does not
        # follow it. Saying "n_layers 3 -> 2" was wrong on both counts.
        text = _readme()
        assert "carrying geometry" in text.lower()
        assert "metadata" in text.lower()

    def test_the_measurement_command_is_named(self):
        assert "doc_claims_check.py" in _readme(), (
            "a published number must name the command that reproduces it")
