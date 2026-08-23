import argparse, asyncio, logging, sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from pyclaw import GatewayServer, GatewayConfig, load as load_config, __version__, __pyclaw_home__
from pyclaw.channels.im import IMChannelAdapter
from pyclaw.config import save as save_config
from pyclaw.cli import stop_server
from chatchat.cli.config import parse_config, cli_config

_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
_CONV_FORMAT = "%(asctime)s | session=%(conv_session)s | %(conv_role)s | %(conv_topic)s | %(conv_detail)s"


class _ConvFormatter(logging.Formatter):
    def __init__(self):
        super().__init__(_CONV_FORMAT)

    def format(self, record):
        for field in ("conv_session", "conv_role", "conv_topic", "conv_detail"):
            if not hasattr(record, field):
                setattr(record, field, "-")
        return super().format(record)


def setup_logging(level: str = "INFO"):
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(getattr(logging, level.upper()))
    console.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(console)

    log_dir = Path(__pyclaw_home__) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_dir / "pyclaw.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(file_handler)

    conv_logger = logging.getLogger("pyclaw.conversation")
    conv_logger.setLevel(logging.DEBUG)
    conv_logger.propagate = False
    conv_handler = RotatingFileHandler(
        log_dir / "conversation.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8",
    )
    conv_handler.setLevel(logging.DEBUG)
    conv_handler.setFormatter(_ConvFormatter())
    conv_logger.addHandler(conv_handler)


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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PyClaw – Personal AI Assistant")
    parser.add_argument("-V", "--version", action="store_true", help="Show version and exit")
    subparsers = parser.add_subparsers(dest="command")

    # --- serve ---
    serve_parser = subparsers.add_parser("serve", help="Start the gateway server")
    serve_parser.add_argument("--port", type=int, default=None, help="HTTP port (config/gateway/http/port or 12321)")
    serve_parser.add_argument("--host", type=str, default=None, help="Bind address (config/gateway/http/host or 127.0.0.1)")
    serve_parser.add_argument("--log-level", type=str, default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    serve_parser.add_argument("--channels", nargs="*", default=None, choices=["web", "qq", "wechat"], help="Channels to enable (default: wechat only)")
    serve_parser.add_argument("--provider", type=str, default=None, help="AI model provider (overrides config)")
    serve_parser.add_argument("--model", type=str, default=None, help="AI model name (overrides config)")

    # --- channel ---
    channel_parser = subparsers.add_parser("channel", help="Manage IM channels")
    channel_sub = channel_parser.add_subparsers(dest="channel_command")
    rebind_parser = channel_sub.add_parser("rebind", help="Rebind (re-authenticate) an IM channel")
    rebind_parser.add_argument(
        "channel", nargs="?", default="wechat", choices=["qq", "wechat"],
        metavar="channel", help="IM channel to rebind (default: wechat)"
    )
    rebind_parser.add_argument("--log-level", type=str, default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    # --- stop ---
    stop_parser = subparsers.add_parser("stop", help="Stop a running gateway (kills listener by port)")
    stop_parser.add_argument("--port", type=int, default=None, help="Port to free (config/gateway/http/port or 12321)")
    stop_parser.add_argument("--force", action="store_true", help="Send SIGKILL instead of SIGTERM")
    stop_parser.add_argument("--all", action="store_true", help="Kill every `pyclaw serve` process, ignoring port")

    # --- config (delegated to chatchat) ---
    cli_config(subparsers)

    parser._channel_parser = channel_parser
    return parser


def _finalize_args(args) -> argparse.Namespace:
    if not hasattr(args, "log_level"):
        args.log_level = "INFO"
    for field in ("provider", "model", "channels", "port", "host"):
        if not hasattr(args, field):
            setattr(args, field, None)
    return args


def main():
    parser = _build_parser()
    args = _finalize_args(parser.parse_args())

    if args.version:
        print(__version__)
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

    if args.command == "serve" or args.command is None:
        asyncio.run(start_server(args))


if __name__ == "__main__":
    main()