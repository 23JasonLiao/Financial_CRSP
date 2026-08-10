from __future__ import annotations

import argparse
from pathlib import Path

from scripts.build_balanced_events import build_all


def main() -> None:
    parser = argparse.ArgumentParser(description="Fin Step 1 - quantify Balanced Fund allocation events")
    parser.add_argument("command", nargs="?", default="serve", choices=["build", "serve", "all"])
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=5000, type=int)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent
    data_root = (project_root / args.data_root).resolve()

    if args.command in {"build", "all"}:
        build_all(data_root=data_root)
        print(f"Built derived data under {data_root / 'derived'}")

    if args.command in {"serve", "all"}:
        from api_server import create_app

        app = create_app(data_root=data_root)
        app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
