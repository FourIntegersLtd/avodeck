"""Where the Liberation and DejaVu fonts live on this machine (Linux CI, or macOS with the font casks).

`EVAL_FONT_DIR` wins when set; otherwise the usual system folders are tried in order. The fonts must be
the same files everywhere, so the rendered pages (and their hashes) do not depend on the machine.
"""
from __future__ import annotations

import os

_SENTINEL = {"liberation": "LiberationSans-Regular.ttf", "dejavu": "DejaVuSans.ttf"}


def font_dir(family: str) -> str:
    candidates = [os.environ.get("EVAL_FONT_DIR", ""), f"/usr/share/fonts/truetype/{family}",
                  os.path.expanduser("~/Library/Fonts"), "/Library/Fonts"]
    for folder in candidates:
        if folder and os.path.exists(os.path.join(folder, _SENTINEL[family])):
            return folder
    raise SystemExit(f"{family} fonts not found; install them or set EVAL_FONT_DIR")
