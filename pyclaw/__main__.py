import argparse, asyncio, json, logging, os, sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from pyclaw import GatewayServer, GatewayConfig, load as load_config, __version__, __pyclaw_home__
from pyclaw.agents import build_team, Session
from pyclaw.channels.im import IMChannelAdapter
from pyclaw.config import save as save_config
from pyclaw.cli import stop_server
from chatchat.cli.config import parse_config, cli_config

_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

_QUIET_LOGGERS = ("asyncio", "markdown_it", "textual", "httpx", "httpcore",
                  "aiohttp", "urllib3", "websockets", "PIL")


def _level(name: str) -> int:
    return getattr(logging, str(name).upper(), logging.INFO)


def setup_logging(level: str = "INFO", *, console: bool = True):
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    if console and not any(
            isinstance(h, logging.StreamHandler)
            and not isinstance(h, RotatingFileHandler)
            for h in root.handlers):
        stream = logging.StreamHandler(sys.stdout)
        stream.setLevel(_level(level))
        stream.setFormatter(logging.Formatter(_LOG_FORMAT))
        root.addHandler(stream)

    log_dir = Path(__pyclaw_home__) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        file_handler = RotatingFileHandler(
            log_dir / "pyclaw.log", maxBytes=5 * 1024 * 1024, backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        root.addHandler(file_handler)

    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    return log_dir / "pyclaw.log"


def _apply_overrides(config: dict, args) -> bool:
    overrides = {
        "provider": args.provider,
        "model": args.model,
        "enabled_channels": args.channels,
    }
    modified = False
    for key, value in overrides.items():
        if value is not None:
            config[key] = value
            modified = True
    return modified


async def start_server(args):
    setup_logging(args.log_level)
    logger = logging.getLogger(__name__)

    config = load_config()
    logger.info("Configuration loaded")

    if _apply_overrides(config, args):
        save_config(config)

    gw_http = config.get("gateway", {}).get("http", {})
    gateway_config = GatewayConfig(
        port=args.port if args.port is not None else gw_http.get("port", 12321),
        host=args.host if args.host is not None else gw_http.get("host", "127.0.0.1"),
        provider=config["provider"],
        model=config["model"],
        enabled_channels=config["enabled_channels"],
    )

    gateway = GatewayServer(gateway_config, app_config=config)
    logger.info(f"Using provider={gateway_config.provider}, model={gateway_config.model}")

    try:
        await gateway.start()
    except KeyboardInterrupt:
        await gateway.shutdown()
    except Exception as e:
        logger.error(f"Gateway error: {e}")
        raise


def _cli_session(args):
    from pyclaw.agents import resolve_session_id
    key = ["cli", os.getcwd()]
    resume_id = getattr(args, "resume", None)
    if resume_id:
        return resolve_session_id(key, rotate=True), resume_id
    if getattr(args, "continue_session", False):
        previous = resolve_session_id(key, rotate=False)
        return resolve_session_id(key, rotate=True), previous
    return resolve_session_id(key, rotate=True), None


def _cli_resume(args) -> bool:
    return bool(getattr(args, "resume", None)
                or getattr(args, "continue_session", False))


async def prompt_once(provider, model, prompt, *, on_event=None,
                      permission_mode='default', session_id=None,
                      resume=False, resume_from=None, allow=None, ask=None,
                      deny=None, use_team=False, agents=None,
                      json_schema=None) -> dict:
    team = build_team(provider, model, permission_mode=permission_mode,
                      allow=allow, ask=ask, deny=deny, use_team=use_team,
                      agents_json=agents)
    if json_schema is not None:
        problem = team.set_output_schema(json_schema)
        if problem:
            raise ValueError(problem)
    session = Session(team, session_id=session_id, resume_from=resume_from)
    try:
        if resume:
            session.restore_transcript()
        text = await session.chat(prompt, on_event=on_event)
        session.record_turn()
        out = {"text": text, "mode": session.mode,
               "permission_mode": session.permission_mode,
               "messages": len(team.transcript()),
               "usage": session.usage.to_dict()}
        if json_schema is not None:
            out["structured_output"] = team.structured_output
        return out
    finally:
        await session.close()


def render_output(output_fmt: str, out: dict):
    if output_fmt == "json":
        print(json.dumps(out, ensure_ascii=False, indent=2))
    elif out.get("structured_output") is not None:
        print(json.dumps(out["structured_output"], ensure_ascii=False))
    else:
        print(out["text"])


def _json_schema(args):
    raw = getattr(args, "json_schema", None)
    if raw is None:
        return None
    if getattr(args, "print", None) is None:
        print("--json-schema needs --print: it shapes the answer of a "
              "non-interactive run.")
        raise SystemExit(1)
    try:
        return json.loads(raw)
    except ValueError:
        print(f"--json-schema is not valid JSON: {raw[:80]}")
        raise SystemExit(1)


async def run_headless(args):
    setup_logging(console=False)
    config = load_config()
    provider = args.provider or config.get("provider")
    model = args.model or config.get("model")
    if not provider or not model:
        print("Provider/model not set. Use --provider/--model or run `pyclaw config` first.")
        sys.exit(1)
    session_id, resume_from = _cli_session(args)
    try:
        out = await prompt_once(provider, model, args.print,
                                permission_mode=args.permission_mode,
                                session_id=session_id, resume=_cli_resume(args),
                                resume_from=resume_from,
                                allow=args.allow, ask=args.ask, deny=args.deny,
                                use_team=args.use_team, agents=args.agents,
                                json_schema=_json_schema(args))
    except ValueError as exc:
        print(f"--json-schema: {exc}")
        sys.exit(1)
    render_output(args.output, out)


def run_tui(args):
    from chatchat.hooks.events import clear_runtime_sinks
    clear_runtime_sinks()
    log_path = setup_logging(console=False)
    config = load_config()
    provider = args.provider or config.get("provider")
    model = args.model or config.get("model")
    if not provider or not model:
        print("Provider/model not set. Use --provider/--model or run `pyclaw config` first.")
        sys.exit(1)
    from pyclaw.tui import PyClawApp
    logging.getLogger(__name__).info(
        "tui session starting (provider=%s model=%s team=%s log=%s)",
        provider, model, args.use_team, log_path)
    session_id, resume_from = _cli_session(args)
    hook_events = bool(args.include_hook_events
                       or config.get("includeHookEvents"))
    PyClawApp(builder=lambda: build_team(
        provider, model, permission_mode=args.permission_mode,
        allow=args.allow, ask=args.ask, deny=args.deny,
        use_team=args.use_team, agents_json=args.agents),
        session_id=session_id,
        resume=_cli_resume(args),
        resume_from=resume_from, hook_events=hook_events).run()


async def run_channel_rebind(args):
    setup_logging(args.log_level)
    adapter = IMChannelAdapter({"platform": args.channel})
    qr_url = None

    def on_qr(url):
        nonlocal qr_url
        qr_url = url

    ok = await adapter.rebind(on_qr_url=on_qr)
    await adapter.disconnect()
    if qr_url:
        print(f"\n请扫描二维码绑定微信:\n{qr_url}\n")
    if ok:
        print(f"Channel '{args.channel}' rebind successfully")
    else:
        print(f"Channel '{args.channel}' rebind failed")
        sys.exit(1)


def stop_server_cmd(args):
    killed = stop_server(port=args.port, force=args.force, all_processes=args.all)
    if not killed:
        print("No running PyClaw gateway found.")
        return
    scope = "all served processes" if args.all else f"port {args.port or 'from config'}"
    print(f"Stopped {scope}: PIDs {', '.join(str(p) for p in killed)}")


def _add_session_args(target):
    target.add_argument("-c", "--continue", dest="continue_session",
                        action="store_true",
                        help="Resume the most recent session in this directory")
    target.add_argument("-r", "--resume", type=str, default=None, metavar="SESSION_ID",
                        help="Resume a specific session by id")
    for flag, help_text in (
            ("--allow", "Permission rule to allow, e.g. 'Bash(git push:*)'"),
            ("--deny", "Permission rule to deny, e.g. 'Bash(curl:*)'"),
            ("--ask", "Permission rule that always asks, e.g. 'Bash(docker:*)'")):
        target.add_argument(flag, action="append", default=None, metavar="RULE",
                            help=help_text)
    target.add_argument("--include-hook-events", action="store_true",
                        default=False,
                        help="Show each hook run as its own line")
    target.add_argument("--use-team", action="store_true", default=False,
                        help="Run in team (multi-agent) mode; fixed for the "
                             "whole session")
    target.add_argument("--agents", type=str, default=None, metavar="JSON",
                        help="Agent definitions as one JSON object keyed by "
                             "name, each with description, prompt, tools, "
                             "model and permissionMode. Wins over the "
                             ".pyclaw/agents files.")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PyClaw – Personal AI Assistant")
    parser.add_argument("-V", "--version", action="store_true", help="Show version and exit")
    parser.add_argument("-p", "--print", type=str, metavar="PROMPT", default=None,
                        help="Print mode: run one prompt non-interactively and print the result")
    parser.add_argument("--output", type=str, default="text", choices=["text", "json"],
                        help="Output format for print mode")
    parser.add_argument("--json-schema", type=str, default=None, metavar="SCHEMA",
                        help="JSON Schema the answer has to match (needs --print). "
                             "The run ends by calling the structured_output tool "
                             "with a payload validated against it.")
    parser.add_argument("--provider", type=str, default=None, help="AI model provider (overrides config)")
    parser.add_argument("--model", type=str, default=None, help="AI model name (overrides config)")
    parser.add_argument("--permission-mode", type=str, default=None,
                        choices=["default", "acceptEdits", "plan"],
                        help="Session permission mode")
    _add_session_args(parser)
    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve", help="Start the gateway server")
    serve_parser.add_argument("--port", type=int, default=None, help="HTTP port (config/gateway/http/port or 12321)")
    serve_parser.add_argument("--host", type=str, default=None, help="Bind address (config/gateway/http/host or 127.0.0.1)")
    serve_parser.add_argument("--log-level", type=str, default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    serve_parser.add_argument("--channels", nargs="*", default=None, choices=["web", "qq", "wechat"], help="Channels to enable (default: wechat only)")
    serve_parser.add_argument("--provider", type=str, default=None, help="AI model provider (overrides config)")
    serve_parser.add_argument("--model", type=str, default=None, help="AI model name (overrides config)")

    channel_parser = subparsers.add_parser("channel", help="Manage IM channels")
    channel_sub = channel_parser.add_subparsers(dest="channel_command")
    rebind_parser = channel_sub.add_parser("rebind", help="Rebind (re-authenticate) an IM channel")
    rebind_parser.add_argument(
        "channel", nargs="?", default="wechat", choices=["qq", "wechat"],
        metavar="channel", help="IM channel to rebind (default: wechat)"
    )
    rebind_parser.add_argument("--log-level", type=str, default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    stop_parser = subparsers.add_parser("stop", help="Stop a running gateway (kills listener by port)")
    stop_parser.add_argument("--port", type=int, default=None, help="Port to free (config/gateway/http/port or 12321)")
    stop_parser.add_argument("--force", action="store_true", help="Send SIGKILL instead of SIGTERM")
    stop_parser.add_argument("--all", action="store_true", help="Kill every `pyclaw serve` process, ignoring port")

    tui_parser = subparsers.add_parser("tui", help="Launch the interactive terminal UI")
    tui_parser.add_argument("--provider", type=str, default=None, help="AI model provider (overrides config)")
    tui_parser.add_argument("--model", type=str, default=None, help="AI model name (overrides config)")
    tui_parser.add_argument("--permission-mode", type=str, default=None,
                            choices=["default", "acceptEdits", "plan"],
                            help="Session permission mode")
    _add_session_args(tui_parser)

    cli_config(subparsers)

    parser._channel_parser = channel_parser
    return parser


def _finalize_args(args) -> argparse.Namespace:
    if not hasattr(args, "log_level"):
        args.log_level = "INFO"
    for field in ("provider", "model", "channels", "port", "host", "resume",
                  "allow", "ask", "deny"):
        if not hasattr(args, field):
            setattr(args, field, None)
    if not hasattr(args, "continue_session"):
        args.continue_session = False
    if not hasattr(args, "use_team") or args.use_team is False:
        args.use_team = bool(load_config().get("agentTeams", False))
    if not hasattr(args, "permission_mode") or args.permission_mode is None:
        args.permission_mode = (load_config().get("permissions", {})
                                .get("defaultMode", "default"))
    return args


def main():
    parser = _build_parser()
    args = _finalize_args(parser.parse_args())

    if args.version:
        print(__version__)
        return

    if args.print is not None:
        asyncio.run(run_headless(args))
        return

    if args.command == "config":
        parse_config(args)
        return

    if args.command == "channel":
        if args.channel_command == "rebind":
            asyncio.run(run_channel_rebind(args))
        else:
            parser._channel_parser.print_help()
        return

    if args.command == "stop":
        stop_server_cmd(args)
        return

    if args.command == "tui":
        run_tui(args)
        return

    if args.command == "serve" or args.command is None:
        asyncio.run(start_server(args))


if __name__ == "__main__":
    main()