"""Configuration loader with file and environment support."""

import json
import os
from pathlib import Path
from typing import Optional, Dict, Any, List
import logging

from .schema import PyClawConfig, ConfigSnapshot

logger = logging.getLogger(__name__)


class ConfigLoader:
    """Loads and manages PyClaw configuration."""
    
    DEFAULT_CONFIG_PATH = Path.home() / ".pyclaw" / "config.json"
    
    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or self.DEFAULT_CONFIG_PATH
        self._cache: Optional[PyClawConfig] = None
        self._snapshot: Optional[ConfigSnapshot] = None
    
    def load(self, force_reload: bool = False) -> PyClawConfig:
        """Load configuration from all sources."""
        if self._cache is not None and not force_reload:
            return self._cache
        
        # 1. Start with defaults
        config_dict = self._get_defaults()
        
        # 2. Load from file if exists
        file_exists = self.config_path.exists()
        file_valid = True
        file_issues: List[str] = []
        
        if file_exists:
            try:
                file_config = self._load_from_file()
                config_dict = self._deep_merge(config_dict, file_config)
            except Exception as e:
                file_valid = False
                file_issues.append(f"Failed to load config file: {e}")
                logger.warning(f"Config file error: {e}")
        
        # 3. Apply environment variable overrides
        config_dict = self._apply_env_overrides(config_dict)
        
        # 4. Validate and create config object
        try:
            config = PyClawConfig.model_validate(config_dict)
        except Exception as e:
            file_valid = False
            file_issues.append(f"Config validation error: {e}")
            logger.error(f"Config validation failed: {e}")
            # Fall back to defaults
            config = PyClawConfig.model_validate(self._get_defaults())
        
        # Create snapshot
        self._snapshot = ConfigSnapshot(
            config=config,
            path=self.config_path,
            exists=file_exists,
            valid=file_valid,
            issues=file_issues,
            legacy_issues=[]
        )
        
        self._cache = config
        return config
    
    def get_snapshot(self) -> ConfigSnapshot:
        """Get the last loaded config snapshot."""
        if self._snapshot is None:
            self.load()
        return self._snapshot
    
    def reload(self) -> PyClawConfig:
        """Reload configuration from disk."""
        self._cache = None
        self._snapshot = None
        return self.load(force_reload=True)
    
    def save(self, config: PyClawConfig) -> None:
        """Save configuration to file."""
        # Ensure directory exists
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Convert to dict and save
        config_dict = config.model_dump(exclude_none=True)
        
        with open(self.config_path, 'w') as f:
            json.dump(config_dict, f, indent=2, default=str)
        
        self._cache = config
        logger.info(f"Configuration saved to {self.config_path}")
    
    def _get_defaults(self) -> Dict[str, Any]:
        """Get default configuration values."""
        return {
            "version": "1.0",
            "gateway": {
                "http": {
                    "enabled": True,
                    "port": 18789,
                    "host": "127.0.0.1",
                    "cors_origins": []
                },
                "websocket": {
                    "enabled": True,
                    "ping_interval": 30,
                    "ping_timeout": 10
                },
                "control_ui": {
                    "enabled": True
                },
                "auth": {}
            },
            "models": {},
            "default_model": None,
            "channels": {},
            "agents": {
                "default": {
                    "name": "Default Agent",
                    "description": "Default PyClaw agent",
                    "model": None,
                    "system_prompt": "You are a helpful AI assistant.",
                    "tools": ["echo", "time"],
                    "memory": True,
                    "max_iterations": 10
                }
            },
            "default_agent": "default",
            "tools": {
                "exec": {
                    "enabled": True,
                    "ask": True,
                    "timeout": 60
                },
                "browser": {
                    "enabled": False
                }
            },
            "sessions": {
                "store_path": "~/.pyclaw/sessions",
                "max_history": 100,
                "ttl_hours": None
            },
            "skills": {
                "enabled": True,
                "auto_enable": False,
                "paths": []
            },
            "logging": {
                "level": "INFO",
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            }
        }
    
    def _load_from_file(self) -> Dict[str, Any]:
        """Load configuration from file."""
        with open(self.config_path, 'r') as f:
            content = f.read()
        
        # Try JSON first, then JSON5
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Try with json5 if available
            try:
                import json5
                return json5.loads(content)
            except ImportError:
                raise ValueError("Config file is not valid JSON and json5 is not installed")
    
    def _deep_merge(self, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """Deep merge two dictionaries."""
        result = base.copy()
        
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        
        return result
    
    def _apply_env_overrides(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Apply environment variable overrides."""
        # Gateway port
        if port := os.getenv("OPENCLAW_GATEWAY_PORT"):
            config["gateway"]["http"]["port"] = int(port)
        
        # Gateway host
        if host := os.getenv("OPENCLAW_GATEWAY_HOST"):
            config["gateway"]["http"]["host"] = host
        
        # Default model
        if model := os.getenv("OPENCLAW_DEFAULT_MODEL"):
            config["default_model"] = model
        
        # Default agent
        if agent := os.getenv("OPENCLAW_DEFAULT_AGENT"):
            config["default_agent"] = agent
        
        # OpenAI API Key
        if api_key := os.getenv("OPENAI_API_KEY"):
            if "openai" not in config["models"]:
                config["models"]["openai"] = {}
            config["models"]["openai"]["api_key"] = api_key
            if "model" not in config["models"]["openai"]:
                config["models"]["openai"]["model"] = "gpt-4"
        
        # Anthropic API Key
        if api_key := os.getenv("ANTHROPIC_API_KEY"):
            if "anthropic" not in config["models"]:
                config["models"]["anthropic"] = {}
            config["models"]["anthropic"]["api_key"] = api_key
            if "model" not in config["models"]["anthropic"]:
                config["models"]["anthropic"]["model"] = "claude-3-opus-20240229"
        
        # Session store path
        if store_path := os.getenv("OPENCLAW_SESSIONS_PATH"):
            config["sessions"]["store_path"] = store_path
        
        # Log level
        if log_level := os.getenv("OPENCLAW_LOG_LEVEL"):
            config["logging"]["level"] = log_level
        
        return config


# Global config loader instance
_config_loader: Optional[ConfigLoader] = None


def get_config_loader() -> ConfigLoader:
    """Get or create the global config loader."""
    global _config_loader
    if _config_loader is None:
        _config_loader = ConfigLoader()
    return _config_loader


def load_config() -> PyClawConfig:
    """Convenience function to load configuration."""
    return get_config_loader().load()


def reload_config() -> PyClawConfig:
    """Convenience function to reload configuration."""
    return get_config_loader().reload()
