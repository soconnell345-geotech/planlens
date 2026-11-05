"""planlens.review — the document tools as a host serves them to a model.

BACKLOG PL-29 (a): the document-review tools that only wrapped planlens moved
here from the GeotechStaffEngineer app. planlens still makes no model call,
gains no dependency and imports nothing of the app: what the app supplied is
now handed over in one :class:`ReviewHost` (its source resolver, its look,
its working folder, its signer, where the markup check keeps its verdicts,
``find_like``'s time budget, and a few optional hooks)::

    from planlens import review

    review.configure(review.ReviewHost(
        resolve_source=my_resolver,           # upload key / file -> bytes or path
        look=lambda image, question, kind: my_vision_model(image, question),
        working_folder=lambda: conversation_folder,
        signer="Reviewer via MyApp (AI draft)"))
    specs = review.tool_specs("anthropic")     # the tools, word for word
    text = review.call("annotate_document", {...}, attachments=uploads)

Modules:

* :mod:`~planlens.review.host` -- :class:`ReviewHost` and :func:`configure`;
* :mod:`~planlens.review.conversation` -- one toolkit per conversation,
  handles and sources interchangeable, viewer page numbers, Word/Excel/DXF
  read as text;
* :mod:`~planlens.review.marked_copy` -- writing, removing and placing marks
  on a copy;
* :mod:`~planlens.review.markup_check` -- every mark looked at on the copy;
* :mod:`~planlens.review.find_like` -- every copy of one mark, each read by
  the look;
* :mod:`~planlens.review.page_numbers` -- 0-based ``page`` / 1-based
  ``pdf_page``;
* :mod:`~planlens.review.frames` -- page and image frames, tiles, zoom
  windows, turned lettering;
* :mod:`~planlens.review.specs` -- the tools' specs;
* :mod:`~planlens.review.tools` -- :func:`call`, the tools run.
"""

from planlens.review.host import (
    ImageSupport, ReviewHost, configure, current,
)
from planlens.review.tools import call, tool_names
from planlens.review.tools import specs as tool_specs

__all__ = ["ReviewHost", "ImageSupport", "configure", "current", "call",
           "tool_specs", "tool_names"]
