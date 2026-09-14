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
"""

from planlens.tools.specs import TOOL_SPECS
from planlens.tools.toolkit import (
    DEFAULT_IMAGE_VIEW_HINT, DEFAULT_MAX_CHARS, DEFAULT_VISION_HINT,
    ReviewToolkit, ToolError,
)

__all__ = ["ReviewToolkit", "ToolError", "TOOL_SPECS", "DEFAULT_MAX_CHARS",
           "DEFAULT_VISION_HINT", "DEFAULT_IMAGE_VIEW_HINT"]
