import argparse, asyncio, logging, sys
from pathlib import Path
from conippets import json
from pyclaw import GatewayServer, GatewayConfig, load as load_config, __secret_file__
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

    try:
        config = load_config(Path(args.config) if args.config else None)
        logger.info("Configuration loaded")
    except Exception as e:
        logger.warning(f"Could not load config: {e}")
        config = None

    gateway_config = GatewayConfig(
        port=args.port,
        host=args.host,
        provider=args.provider,
        model=args.model
    )

    if config and config.get("gateway"):
        gw = config["gateway"]
        gateway_config.port = gw.get("http", {}).get("port", gateway_config.port)
        gateway_config.host = gw.get("http", {}).get("host", gateway_config.host)
        gateway_config.control_ui_enabled = gw.get("control_ui", {}).get("enabled", True)

    gateway = GatewayServer(gateway_config)

    try:
        await gateway.start()
    except KeyboardInterrupt:
        await gateway.shutdown()
    except Exception as e:
        logger.error(f"Gateway error: {e}")
        raise


def main():
    parser = argparse.ArgumentParser(description="PyClaw Python Gateway")
    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve", help="Start the gateway server")
    serve_parser.add_argument("--port", type=int, default=12321, help="Gateway port")
    serve_parser.add_argument("--host", type=str, default="127.0.0.1", help="Gateway host")
    serve_parser.add_argument("--config", type=str, help="Path to config file")
    serve_parser.add_argument("--log-level", type=str, default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    serve_parser.add_argument("--provider", type=str, default="tencent", help="AI model provider")
    serve_parser.add_argument("--model", type=str, default="hunyuan-lite", help="AI model name")

    cli_config(subparsers)

    args = parser.parse_args()

    if args.command == "config":
        if not Path(__secret_file__).exists():
            Path(__secret_file__).parent.mkdir(parents=True, exist_ok=True)
            json.write(__secret_file__, {})
        parse_config(args, secret_file=__secret_file__)
        return

    if args.command is None or args.command == "serve":
        asyncio.run(start_server(args))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nShutdown complete")
