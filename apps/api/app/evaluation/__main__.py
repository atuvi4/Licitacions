"""Permet `python -m app.evaluation`. La implementacio viu a `requirements.py`,
que es el nom documentat: `python -m app.evaluation.requirements`.
"""

from __future__ import annotations

import sys

from app.evaluation.requirements import main

if __name__ == "__main__":
    sys.exit(main())
