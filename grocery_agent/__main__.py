"""Usage:
    python -m grocery_agent update   # download latest prices and rebuild the database
    python -m grocery_agent          # start chatting
"""
import sys

from . import db


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "chat"
    if cmd == "update":
        print("Downloading prices from Checkjebon…")
        db.download()
        info = db.build()
        print(f"Data date {info['data_date']}; {sum(info['counts'].values())} products; "
              f"pack size parsed for {info['size_parsed_ratio']:.0%}")
        for store, n in sorted(info["counts"].items()):
            print(f"  {store:<10} {n}")
    elif cmd == "chat":
        from .agent import chat
        chat()
    else:
        print(__doc__)


main()
