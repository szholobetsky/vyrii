"""vyrii entry point.

Usage:
  vyrii                                        Flask API + HTML UI on port 5000
  vyrii -p 8002                                Flask on custom port
  vyrii --api                                  FastAPI on port 5001  (requires pip install vyrii[api])
  vyrii --ui                                   Gradio UI on port 4896 (requires pip install vyrii[gradio])
  vyrii --ui 8001 --api 5001                   Gradio + FastAPI together
  vyrii --host localhost:11434                  custom Ollama host
  vyrii --host openai://localhost:1234          OpenAI-compatible backend
  vyrii --lang uk --model qwen2.5:7b            language + default model
  vyrii --bind 127.0.0.1                        listen on localhost only
"""
from __future__ import annotations

import argparse


def _parse_host(host_str: str) -> tuple[str, str]:
    """Parse host string like 1bcoder does.

    Accepts:
      localhost:11434          → ollama, http://localhost:11434
      ollama://localhost:11434 → ollama, http://localhost:11434
      openai://localhost:1234  → openai, http://localhost:1234
    """
    from .engine import BACKEND_OLLAMA, BACKEND_OPENAI
    if host_str.startswith("openai://"):
        url = "http://" + host_str[len("openai://"):]
        return url, BACKEND_OPENAI
    if host_str.startswith("ollama://"):
        url = "http://" + host_str[len("ollama://"):]
        return url, BACKEND_OLLAMA
    # plain host:port or full http:// url
    if not host_str.startswith("http"):
        host_str = "http://" + host_str
    return host_str, BACKEND_OLLAMA


def _load_config() -> dict:
    import json as _json
    import pathlib as _pl
    try:
        p = _pl.Path.home() / ".vyrii" / "config.json"
        if p.exists():
            return _json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="vyrii",
        description="Local AI tools",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-p", "--port", type=int, metavar="PORT", default=None,
                        help="Flask server port (default: 5000)")
    parser.add_argument("--ui",  type=int, metavar="PORT", nargs="?", const=4896,
                        help="Gradio UI port (default: 4896) — requires pip install vyrii[gradio]")
    parser.add_argument("--api", type=int, metavar="PORT", nargs="?", const=5001,
                        help="FastAPI port (default: 5001) — requires pip install vyrii[api]")
    parser.add_argument("--bind", default="0.0.0.0", metavar="ADDR",
                        help="Network interface to listen on (default: 0.0.0.0)")
    parser.add_argument("--auth", action="store_true",
                        help="Enable HTTP Basic Auth (credentials in ~/.vyrii/config.json, default admin/admin)")
    parser.add_argument("--host", default=None, metavar="HOST",
                        help="LLM server — host:port, ollama://host:port, or openai://host:port")
    parser.add_argument("--ollama", default="http://localhost:11434", metavar="URL",
                        help="Ollama base URL (legacy, use --host instead)")
    parser.add_argument("--openai", default=None, metavar="URL",
                        help="OpenAI-compatible server URL")
    parser.add_argument("--lang", default=None, metavar="LANG",
                        help="UI language: en, uk (overrides saved config)")
    parser.add_argument("--model", default=None, metavar="MODEL",
                        help="Default model to select on startup")
    args = parser.parse_args()

    # default: Flask server if no mode flag given
    if args.ui is None and args.api is None:
        if args.port is None:
            args.port = 5000

    from .engine import BACKEND_OLLAMA, BACKEND_OPENAI

    # resolve LLM backend
    if args.openai:
        api_url, api_backend = args.openai, BACKEND_OPENAI
    elif args.host:
        api_url, api_backend = _parse_host(args.host)
    else:
        api_url, api_backend = args.ollama, BACKEND_OLLAMA

    # resolve language: CLI arg > config.json > "en"
    cfg = _load_config()
    lang = args.lang or cfg.get("lang", "en")

    # resolve model: CLI arg > config.json saved_model > None (auto from server)
    startup_model = args.model or cfg.get("saved_model", None)

    if args.ui and args.api:
        _run_both(args, api_url, api_backend, lang, startup_model)
    elif args.api:
        _run_api_only(args, api_url, api_backend)
    elif args.ui:
        _run_ui_only(args, api_url, api_backend, lang, startup_model)
    else:
        _run_flask(args, api_url, api_backend)


def _run_flask(args, api_url, api_backend) -> None:
    try:
        from .flask_api import create_app
    except ImportError:
        print("Flask not found — run: pip install vyrii")
        raise SystemExit(1)
    app = create_app(base_url=api_url, backend=api_backend, auth=args.auth)
    port = args.port or 5000
    print(f"vyrii -> http://localhost:{port}  (UI: /ui/)")
    print(f"  backend : {api_url}  ({api_backend})")
    print(f"  auth    : {'on' if args.auth else 'off'}")
    app.run(host=args.bind, port=port, threaded=True)


def _run_ui_only(args, api_url, api_backend, lang, startup_model) -> None:
    try:
        from .app import main as _gradio
    except ImportError:
        print("Gradio UI requires: pip install vyrii[gradio]")
        raise SystemExit(1)
    _gradio(port=args.ui, host=args.bind,
            ollama_url=api_url,
            openai_url=args.openai or "http://localhost:8080",
            lang=lang,
            startup_model=startup_model,
            auth=args.auth)


def _run_api_only(args, api_url, api_backend) -> None:
    try:
        import uvicorn
        from .api import create_app
    except ImportError:
        print("FastAPI server requires: pip install vyrii[api]")
        raise SystemExit(1)
    port = args.api or 5001
    print(f"vyrii FastAPI -> http://localhost:{port}")
    print(f"  backend : {api_url}  ({api_backend})")
    print(f"  auth    : {'on' if args.auth else 'off'}")
    print(f"  docs    : http://localhost:{port}/docs")
    uvicorn.run(create_app(base_url=api_url, backend=api_backend, auth=args.auth),
                host=args.bind, port=port)


def _run_both(args, api_url, api_backend, lang, startup_model) -> None:
    import threading
    try:
        import uvicorn
        from .api import create_app
    except ImportError:
        print("FastAPI server requires: pip install vyrii[api]")
        raise SystemExit(1)
    try:
        from .app import build_app, _vyrii_auth
    except ImportError:
        print("Gradio UI requires: pip install vyrii[gradio]")
        raise SystemExit(1)

    gradio_app = build_app(
        ollama_url=api_url,
        openai_url=args.openai or "http://localhost:8080",
        lang=lang,
        startup_model=startup_model,
    )

    def _launch_gradio():
        gradio_app.launch(
            server_name=args.bind, server_port=args.ui,
            prevent_thread_lock=True,
            auth=_vyrii_auth if args.auth else None,
            theme=getattr(gradio_app, "_vyrii_theme", None),
        )

    t = threading.Thread(target=_launch_gradio, daemon=True)
    t.start()
    port = args.api or 5001
    print(f"vyrii UI  -> http://localhost:{args.ui}")
    print(f"vyrii API -> http://localhost:{port}  (docs: /docs)")
    print(f"  auth    : {'on' if args.auth else 'off'}")
    uvicorn.run(create_app(base_url=api_url, backend=api_backend, auth=args.auth),
                host=args.bind, port=port)


if __name__ == "__main__":
    main()
