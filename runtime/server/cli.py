from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from runtime.server.app import create_app


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start the Lucode Runtime Server.")
    parser.add_argument(
        "--workspace",
        default=".",
        help="Lucode workspace root. Defaults to the current directory.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host. Keep this on 127.0.0.1 for local desktop use.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Bind port. Use 0 to let the OS choose a free port.",
    )
    return parser


def build_app_from_args(args: argparse.Namespace):
    return create_app(workspace_root=Path(args.workspace).resolve())


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        import uvicorn
    except ModuleNotFoundError as exc:
        raise SystemExit("缺少 server 依赖：请安装 lucode[server] 后再启动 Runtime Server。") from exc

    uvicorn.run(build_app_from_args(args), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
