import argparse, asyncio, logging, sys
from pathlib import Path
from conippets import json
from pyclaw import GatewayServer, GatewayConfig, load as load_config, __secret_file__, __version__
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

    gw = config.get("gateway", {})
    gateway_config = GatewayConfig(
        port=gw.get("http", {}).get("port", args.port),
        host=gw.get("http", {}).get("host", args.host),
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


def main():
    parser = argparse.ArgumentParser(description="PyClaw Python Gateway")
    parser.add_argument("-V", "--version", action="store_true", help="Show version and exit")
    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve", help="Start the gateway server")
    serve_parser.add_argument("--port", type=int, default=12321, help="Gateway port")
    serve_parser.add_argument("--host", type=str, default="127.0.0.1", help="Gateway host")
    serve_parser.add_argument("--log-level", type=str, default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    serve_parser.add_argument("--channels", nargs="*", default=None, choices=["web", "qq", "wechat"], help="Channels to enable (default: web only)")
    serve_parser.add_argument("--provider", type=str, default=None, help="AI model provider")
    serve_parser.add_argument("--model", type=str, default=None, help="AI model name")

    cli_config(subparsers)

    args = parser.parse_args()

    if args.version:
        print(__version__)
        return

    if args.command == "config":
        if not Path(__secret_file__).exists():
            Path(__secret_file__).parent.mkdir(parents=True, exist_ok=True)
            json.write(__secret_file__, {})
        parse_config(args, secret_file=__secret_file__)
        return

    if args.command is None or args.command == "serve":
        asyncio.run(start_server(args))


if __name__ == "__main__":
    main()
