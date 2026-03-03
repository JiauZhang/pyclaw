"""Tests for config module."""

import pytest
import tempfile
import os
from pathlib import Path
from pyclaw.config import ConfigLoader, load_config, reload_config
from pyclaw.config.schema import PyClawConfig


class TestConfigLoader:
    """Test ConfigLoader class."""
    
    def test_default_config(self):
        """Test loading default configuration."""
        loader = ConfigLoader()
        config = loader.load()
        assert isinstance(config, PyClawConfig)
        assert config.gateway.http.port == 12321
        assert config.gateway.http.host == "127.0.0.1"
    
    def test_load_from_file(self):
        """Test loading configuration from file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write('''{
                "gateway": {
                    "http": {
                        "port": 8080,
                        "host": "0.0.0.0"
                    }
                }
            }''')
            temp_file = f.name
        
        try:
            loader = ConfigLoader(Path(temp_file))
            config = loader.load()
            assert config.gateway.http.port == 8080
            assert config.gateway.http.host == "0.0.0.0"
        finally:
            os.unlink(temp_file)
    
    def test_env_overrides(self):
        """Test environment variable overrides."""
        os.environ["OPENCLAW_GATEWAY_PORT"] = "9999"
        os.environ["OPENCLAW_GATEWAY_HOST"] = "localhost"
        
        try:
            loader = ConfigLoader()
            config = loader.load(force_reload=True)
            assert config.gateway.http.port == 9999
            assert config.gateway.http.host == "localhost"
        finally:
            del os.environ["OPENCLAW_GATEWAY_PORT"]
            del os.environ["OPENCLAW_GATEWAY_HOST"]
    
    def test_save_config(self):
        """Test saving configuration to file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_file = f.name
        
        try:
            loader = ConfigLoader(Path(temp_file))
            config = loader.load()
            config.gateway.http.port = 7777
            loader.save(config)
            
            # Reload and verify
            new_loader = ConfigLoader(Path(temp_file))
            new_config = new_loader.load()
            assert new_config.gateway.http.port == 7777
        finally:
            os.unlink(temp_file)
    
    def test_reload_config(self):
        """Test reloading configuration."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write('''{
                "gateway": {
                    "http": {
                        "port": 8080
                    }
                }
            }''')
            temp_file = f.name
        
        try:
            loader = ConfigLoader(Path(temp_file))
            config = loader.load()
            assert config.gateway.http.port == 8080
            
            # Modify the file
            with open(temp_file, 'w') as f:
                f.write('''{
                    "gateway": {
                        "http": {
                            "port": 9090
                        }
                    }
                }''')
            
            # Reload and verify
            config = loader.reload()
            assert config.gateway.http.port == 9090
        finally:
            os.unlink(temp_file)


def test_load_config_function():
    """Test load_config function."""
    config = load_config()
    assert isinstance(config, PyClawConfig)


def test_reload_config_function():
    """Test reload_config function."""
    config = reload_config()
    assert isinstance(config, PyClawConfig)
