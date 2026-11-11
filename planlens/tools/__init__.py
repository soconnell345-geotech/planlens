"""planlens.tools — the document layer as tools an LLM can call.

Framework-neutral: publish :meth:`ReviewToolkit.specs` to the model in the
style your API expects, and route each tool call to
:meth:`ReviewToolkit.call_json`, which always returns valid JSON inside the
size limit you set::

    from planlens.tools import ReviewToolkit

    kit = ReviewToolkit(resolve_source=lambda key: uploads[key], max_chars=7500)
    tools = kit.specs("anthropic")          # or "openai"
    ...
    result_text = kit.call_json(tool_name, tool_arguments)

``look`` (next to ``resolve_source``) is the host's way of letting a tool see
an image: ``(image, question, kind) -> the model's text``. planlens still
makes no model call. The host layer built on the toolkit (one toolkit per
conversation, the markup check, ``find_like`` with every candidate read) is
:mod:`planlens.review`.
"""

from planlens.tools.specs import SHEET_TOOL_SPECS, TOOL_SPECS
from planlens.tools.toolkit import (
    DEFAULT_IMAGE_VIEW_HINT, DEFAULT_MAX_CHARS, DEFAULT_VISION_HINT, Look,
    ReviewToolkit, ToolError,
)

__all__ = ["ReviewToolkit", "ToolError", "Look", "TOOL_SPECS",
           "SHEET_TOOL_SPECS",
           "DEFAULT_MAX_CHARS",
           "DEFAULT_VISION_HINT", "DEFAULT_IMAGE_VIEW_HINT"]
