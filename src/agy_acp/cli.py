from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from agy_acp import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agy-acp",
        description="ACP v1 adapter for the Antigravity CLI",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    build_parser().parse_args(argv)
    print("agy-acp: the ACP server is not implemented yet", file=sys.stderr)
    return 2
