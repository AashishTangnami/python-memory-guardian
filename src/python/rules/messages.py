"""Message text from the shared messages.json; the Rust server embeds the same file."""
from __future__ import annotations

import json
import os
import re


with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "messages.json"), encoding="utf-8") as _f:
    MESSAGES: dict = json.load(_f)

_PH = re.compile(r"\{([a-z0-9_]+)\}")


def render(key: str, facts: dict, local: dict) -> str:
    """Use the 'sized' text only if every placeholder is known; else 'neutral'."""
    values = {k: str(v) for k, v in facts.items()}
    values.update(local)
    msg = MESSAGES[key]
    sized = msg.get("sized")
    if sized and all(p in values for p in _PH.findall(sized)):
        return _PH.sub(lambda m: values[m.group(1)], sized)
    return _PH.sub(lambda m: values.get(m.group(1), m.group(0)), msg["neutral"])
