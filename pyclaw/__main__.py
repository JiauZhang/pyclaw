"""PyClaw CLI entry point."""

import argparse, asyncio, logging, sys
from pyclaw import GatewayServer, GatewayConfig, load as load_config, __version__
from pyclaw.config import save as save_config
from chatchat.cli.config import parse_config, cli_config


def setup_logging(level: str = "INFO"):
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)]
    )


async def start_server(args):
    setup_logging(args.log_level)
    logger = logging.getLogger(__name__)

    config = load_config()
    logger.info("Configuration loaded")

    modified = False
    if args.provider is not None:
        config["provider"] = args.provider
        modified = True
    if args.model is not None:
        config["model"] = args.model
        modified = True
    if args.channels is not None:
        config["enabled_channels"] = args.channels
        modified = True

    if modified:
        save_config(config)

    # Priority: CLI explicit arg > config file > hardcoded default
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
    except SystemExit:
        logger.error("Gateway start failed")
        raise
    except Exception as e:
        logger.error(f"Gateway error: {e}")
        raise


async def run_channel_rebind(args):
    setup_logging(args.log_level)
    from pyclaw.channels.im import IMChannelAdapter

    adapter = IMChannelAdapter({"platform": args.channel})
    qr_url = None

    def on_qr(url):
        nonlocal qr_url
        qr_url = url

    ok = await adapter.rebind(on_qr_url=on_qr)
    # The adapter was only needed for rebind – shut down its client cleanly
    # to avoid "Unclosed client session" warnings.
    await adapter.disconnect()
    if qr_url:
        print(f"\n请扫描二维码绑定微信:\n{qr_url}\n")
    if ok:
        print(f"Channel '{args.channel}' rebind successfully")
    else:
        print(f"Channel '{args.channel}' rebind failed")
        sys.exit(1)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PyClaw – Personal AI Assistant")
    parser.add_argument("-V", "--version", action="store_true", help="Show version and exit")
    subparsers = parser.add_subparsers(dest="command")

    # --- serve ---
    serve_parser = subparsers.add_parser("serve", help="Start the gateway server")
    serve_parser.add_argument("--port", type=int, default=None, help="HTTP port (config/gateway/http/port or 12321)")
    serve_parser.add_argument("--host", type=str, default=None, help="Bind address (config/gateway/http/host or 127.0.0.1)")
    serve_parser.add_argument("--log-level", type=str, default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    serve_parser.add_argument("--channels", nargs="*", default=None, choices=["web", "qq", "wechat"], help="Channels to enable (default: web only)")
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

    # --- config (delegated to chatchat) ---
    cli_config(subparsers)

    parser._channel_parser = channel_parser
    return parser


def main():
    parser = _build_parser()
    args = parser.parse_args()

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

    if args.command == "serve" or args.command is None:
        asyncio.run(start_server(args))


if __name__ == "__main__":
    main()