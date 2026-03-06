"""
Commission document loader.

Reads all .md files from /commission_docs/ (the Modal-mounted docs/Commissions folder)
and returns them as a single combined text block.

Used by the flight extractor to pass real, up-to-date commission tables to the agent
instead of hardcoded rate summaries.

Adding a new commission document or promotion: drop a .md file into docs/Commissions/
and run `modal deploy app/main.py`. No code changes required.
"""

import os

# Path where docs/Commissions/ is mounted inside the Modal container.
# Set by add_local_dir() in main.py.
_COMMISSION_DOCS_PATH = "/commission_docs"


def load_all() -> str:
    """Load all commission .md files and return as a single labelled text block.

    Returns an empty string if the directory does not exist (e.g. running
    outside the Modal container during local syntax checks).
    """
    if not os.path.isdir(_COMMISSION_DOCS_PATH):
        return ""

    docs = []
    for filename in sorted(os.listdir(_COMMISSION_DOCS_PATH)):
        if not filename.lower().endswith(".md"):
            continue
        filepath = os.path.join(_COMMISSION_DOCS_PATH, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                docs.append(f"--- {filename} ---\n{content}")
        except OSError:
            continue

    return "\n\n".join(docs)
