"""Compatibility entry point for the authenticated generated-image swipe workflow."""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.generated_image_review_app.swipe import *  # noqa: F403

if __name__ == "__main__":
    main()
