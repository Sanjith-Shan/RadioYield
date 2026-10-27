"""CLI: python -m radioyield.uwb {fetch,losnlos,ranging}"""
from __future__ import annotations

import json
import sys


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in {"fetch", "losnlos", "ranging"}:
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "fetch":
        from .fetch import main as fetch_main
        return fetch_main(rest)
    if cmd == "losnlos":
        from .losnlos import run
    else:
        from .ranging import run
    res = run()
    keys = [k for k in res if k not in ("los_vs_nlos_feature_medians",)]
    print(json.dumps({k: res[k] for k in keys}, indent=1)[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
