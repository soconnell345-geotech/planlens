"""Capture the app's specs of the tools that moved into ``planlens.review``.

BACKLOG PL-29 (a): the tool specs that move keep the app's descriptions and
input schemas word for word. This script builds the app's own tools (its
``make_vision_tools``, offline, on a fake engine) for the moved tools and
writes each one's name, description and input schema -- as the agent
framework hands them to the model -- to ``app_tool_specs.json`` beside it.
``test_host_specs.py`` checks that :func:`planlens.review.specs.host_specs`
equals that file.

Run it from a checkout of the app, with this planlens installed::

    python planlens/review/tests/capture_app_specs.py --app ../GeotechStaffEngineer

Re-run it only when the app's specs change on purpose; the fixture records
the app commit and the planlens version it was taken from.
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "app_tool_specs.json")

#: The app tools whose code moved into planlens.review (PL-29 (a)).
MOVED_TOOLS = (
    "open_document", "document_structure", "document_page_map",
    "read_document", "search_document", "find_quantities",
    "document_markups", "render_page_thumbnails", "annotate_document",
    "find_like",
)


class _FakeEngine:
    """Enough of a vision engine for the app to offer its vision tools."""

    def analyze_image(self, *args, **kwargs):           # pragma: no cover
        return ""

    def chat(self, *args, **kwargs):                    # pragma: no cover
        return ""


def _schema(tool):
    schema = tool.tool_call_schema
    if not isinstance(schema, dict):
        schema = schema.model_json_schema()
    return schema


def capture(app_dir: str) -> dict:
    sys.path.insert(0, os.path.abspath(app_dir))
    os.environ.setdefault("GEOTECH_VISION_PROBE", "0")
    from funhouse_agent.deep.vision_kit import make_vision_tools
    import planlens

    tools = make_vision_tools(engine=_FakeEngine(), attachments={},
                              include=set(MOVED_TOOLS))
    specs = {}
    for t in tools:
        specs[t.name] = {"description": inspect.cleandoc(t.description),
                         "input_schema": _schema(t)}
    missing = sorted(set(MOVED_TOOLS) - set(specs))
    if missing:
        raise SystemExit(f"the app did not build: {missing}")
    try:
        commit = subprocess.run(
            ["git", "-C", app_dir, "rev-parse", "HEAD"], capture_output=True,
            text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001 - recorded as unknown
        commit = "unknown"
    return {"_about": ("The app's specs of the tools that moved into "
                       "planlens.review (BACKLOG PL-29 (a)), written by "
                       "capture_app_specs.py. Do not edit by hand."),
            "app_commit": commit,
            "planlens_version": getattr(planlens, "__version__", "?"),
            "tools": {name: specs[name] for name in MOVED_TOOLS}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--app", required=True,
                    help="the app checkout (GeotechStaffEngineer)")
    ap.add_argument("--out", default=FIXTURE)
    args = ap.parse_args(argv)
    data = capture(args.app)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=False, ensure_ascii=False)
        fh.write("\n")
    print(f"wrote {len(data['tools'])} specs to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
