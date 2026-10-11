"""Start the local posting dashboard server."""

import argparse
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from automation.config_loader import load_config
from automation.dashboard_server import make_server


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("rakuten-room-kai"))
    parser.add_argument("--port", type=int, default=load_config()["dashboard_port"])
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    server = make_server(args.root, args.port)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(url)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
