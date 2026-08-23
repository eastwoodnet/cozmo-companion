#!/usr/bin/env python
"""Cozmo Companion launcher.

Usage:
    python run.py                     # start on http://127.0.0.1:8000
    python run.py --port 9000
    python run.py --lang en --ollama-model llama3.2
    python run.py --no-browser
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import webbrowser


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Cozmo Companion — reemplazo moderno de la app de Cozmo (pycozmo + web)"
    )
    parser.add_argument("--host", default=None, help="Host/interface (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="Port (default: 8000)")
    parser.add_argument("--lang", choices=["es", "en"], default=None, help="UI/log language")
    parser.add_argument("--ollama-url", default=None, help="Ollama base URL")
    parser.add_argument("--ollama-model", default=None, help="Ollama model (default: phi3)")
    parser.add_argument(
        "--llm-api-key",
        default=None,
        help="API key para un LLM OpenAI-compatible (activa modo nube; mejor vía COZMO_LLM_API_KEY)",
    )
    parser.add_argument("--vosk-model", default=None, help="Path to a Vosk model directory")
    parser.add_argument("--fps", type=float, default=None, help="Camera frames per second (default: 5)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the browser")
    parser.add_argument("--log-level", default="INFO", help="DEBUG, INFO, WARNING...")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    # Windows consoles default to cp1252 which cannot print emojis.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    args = parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Deferred imports so --help works even without dependencies installed.
    try:
        import uvicorn
    except ImportError:
        print("Faltan dependencias. Instálalas con:\n    pip install -r requirements.txt")
        return 1

    from companion.config import Config
    from companion.server import create_app

    config = Config()
    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port
    if args.lang:
        config.language = args.lang
    if args.ollama_url:
        config.ollama_url = args.ollama_url
    if args.ollama_model:
        config.ollama_model = args.ollama_model
    if args.llm_api_key:
        config.llm_api_key = args.llm_api_key
    if args.vosk_model:
        from pathlib import Path
        config.vosk_model = Path(args.vosk_model)
    if args.fps:
        config.camera_fps = args.fps

    url = f"http://{config.host}:{config.port}"
    llm_mode = "nube (OpenAI-compatible)" if config.llm_api_key else "Ollama local"
    print(f"""
    🤖 Cozmo Companion
    ─────────────────────────────────────────
    Panel web:   {url}
    Idioma:      {config.language}
    LLM:         {config.ollama_model} @ {config.ollama_url} [{llm_mode}]
    Voz (Vosk):  {config.vosk_model or 'no encontrado'}
    ─────────────────────────────────────────
    Conecta tu PC a la WiFi de Cozmo y pulsa «Conectar» en la web.
    """)

    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    app = create_app(config)
    uvicorn.run(app, host=config.host, port=config.port, log_level=args.log_level.lower())
    return 0


if __name__ == "__main__":
    sys.exit(main())
