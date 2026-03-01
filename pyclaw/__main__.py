import argparse, asyncio, logging, sys
from pathlib import Path
from pyclaw import GatewayServer, GatewayConfig, ConfigLoader


def setup_logging(level: str = "INFO"):
    """Setup logging configuration."""
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )


def create_sample_config():
    """Create a sample configuration file."""
    config_path = Path.home() / ".openclaw" / "config.json"
    
    if config_path.exists():
        print(f"Config file already exists at {config_path}")
        return
    
    config_path.parent.mkdir(parents=True, exist_ok=True)
    
    sample_config = """{
  "version": "1.0",
  "gateway": {
    "http": {
      "port": 18789,
      "host": "127.0.0.1"
    },
    "control_ui": {
      "enabled": true
    }
  },
  "models": {
    "openai": {
      "provider": "openai",
      "model": "gpt-4",
      "api_key": null
    }
  },
  "default_model": "openai",
  "agents": {
    "default": {
      "name": "Default Agent",
      "system_prompt": "You are a helpful AI assistant.",
      "tools": ["echo", "time"],
      "memory": true
    }
  },
  "channels": {},
  "sessions": {
    "store_path": "~/.openclaw/sessions",
    "max_history": 100
  }
}"""
    
    with open(config_path, 'w') as f:
        f.write(sample_config)
    
    print(f"Sample config created at {config_path}")
    print("Please edit it to add your API keys and preferences.")


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="OpenClaw Python Gateway",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                           # Start with default settings
  %(prog)s --port 8080               # Start on port 8080
  %(prog)s --host 0.0.0.0            # Listen on all interfaces
  %(prog)s --provider deepseek       # Use DeepSeek provider
  %(prog)s --provider openai --model gpt-4  # Use OpenAI with GPT-4
  %(prog)s --init-config             # Create sample config file
        """
    )
    
    parser.add_argument(
        "--port",
        type=int,
        default=18789,
        help="Gateway port (default: 18789)"
    )
    
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Gateway host (default: 127.0.0.1)"
    )
    
    parser.add_argument(
        "--config",
        type=str,
        help="Path to config file"
    )
    
    parser.add_argument(
        "--init-config",
        action="store_true",
        help="Create a sample configuration file"
    )
    
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)"
    )
    
    parser.add_argument(
        "--provider",
        type=str,
        default=None,
        help="AI model provider (deepseek, openai, alibaba, etc.)"
    )
    
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="AI model name (e.g., deepseek-chat, gpt-4, gpt-3.5-turbo)"
    )
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging(args.log_level)
    logger = logging.getLogger(__name__)
    
    # Handle init-config
    if args.init_config:
        create_sample_config()
        return
    
    # Load configuration
    if args.config:
        config_loader = ConfigLoader(Path(args.config))
    else:
        config_loader = ConfigLoader()
    
    try:
        config = config_loader.load()
        logger.info(f"Loaded configuration from {config_loader.config_path}")
    except Exception as e:
        logger.warning(f"Could not load config: {e}")
        logger.info("Using default configuration")
        config = None
    
    # Create gateway config
    gateway_config = GatewayConfig(
        port=args.port,
        host=args.host,
        control_ui_enabled=True,
        provider=args.provider,
        model=args.model
    )
    
    # Override from config file if available
    if config and config.gateway:
        gateway_config.port = config.gateway.http.port
        gateway_config.host = config.gateway.http.host
        gateway_config.control_ui_enabled = config.gateway.control_ui.get("enabled", True)
    
    # Override from command line
    gateway_config.port = args.port
    gateway_config.host = args.host
    
    # Create and start gateway
    gateway = GatewayServer(gateway_config)
    
    try:
        await gateway.start()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
        await gateway.shutdown()
    except Exception as e:
        logger.error(f"Gateway error: {e}")
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown complete")
        sys.exit(0)
