from __future__ import annotations

import argparse
import json
from pathlib import Path

import uvicorn

from klipper_cnc_assistant.api import create_app
from klipper_cnc_assistant.api.schemas import analysis_to_response
from klipper_cnc_assistant.application import ProjectService
from klipper_cnc_assistant.storage import JsonProjectRepository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m klipper_cnc_assistant",
        description=(
            "Herramientas de linea de comandos de Klipper CNC Assistant."
        ),
    )
    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser(
        "serve",
        help="Inicia la API HTTP.",
    )
    serve_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host de escucha de la API.",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Puerto de escucha de la API.",
    )
    serve_parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Directorio raiz de datos JSON.",
    )

    check_parser = subparsers.add_parser(
        "check-gcode",
        help="Analiza un archivo G-code sin mover hardware.",
    )
    check_parser.add_argument(
        "archivo",
        type=Path,
        help="Ruta del archivo G-code a analizar.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "serve":
        app = create_app(data_dir=args.data_dir)
        uvicorn.run(app, host=args.host, port=args.port)
        return 0

    if args.command == "check-gcode":
        service = ProjectService(JsonProjectRepository(Path("data")))
        analysis = service.check_gcode_file(args.archivo)
        print(
            json.dumps(
                analysis_to_response(analysis).model_dump(),
                ensure_ascii=True,
                indent=2,
            )
        )
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
