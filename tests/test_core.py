"""Tests for core module."""

import pytest
import sys
import tempfile
import os
from pathlib import Path
from pyclaw.__main__ import create_sample_config, setup_logging


class TestCoreFunctions:
    """Test core functions from __main__.py."""
    
    def test_setup_logging(self):
        """Test setup_logging function."""
        # This is a simple test to ensure the function doesn't raise exceptions
        setup_logging("INFO")
        setup_logging("DEBUG")
        setup_logging("WARNING")
        setup_logging("ERROR")
    
    def test_create_sample_config(self):
        """Test create_sample_config function."""
        # Create a temporary directory for testing
        with tempfile.TemporaryDirectory() as temp_dir:
            # Override the default config path
            original_home = os.environ.get("HOME")
            os.environ["HOME"] = temp_dir
            
            try:
                # Run create_sample_config
                create_sample_config()
                
                # Check if config file was created
                config_path = Path(temp_dir) / ".pyclaw" / "config.json"
                assert config_path.exists()
                
                # Check if config file has expected content
                with open(config_path, 'r') as f:
                    content = f.read()
                assert "version" in content
                assert "gateway" in content
                assert "models" in content
            finally:
                # Restore original HOME
                if original_home:
                    os.environ["HOME"] = original_home
    
    def test_create_sample_config_existing(self):
        """Test create_sample_config function when config already exists."""
        # Create a temporary directory for testing
        with tempfile.TemporaryDirectory() as temp_dir:
            # Override the default config path
            original_home = os.environ.get("HOME")
            os.environ["HOME"] = temp_dir
            
            try:
                # Create config directory and file
                config_dir = Path(temp_dir) / ".pyclaw"
                config_dir.mkdir(parents=True, exist_ok=True)
                config_path = config_dir / "config.json"
                with open(config_path, 'w') as f:
                    f.write('{}')
                
                # Run create_sample_config
                create_sample_config()
                
                # Check if config file still exists
                assert config_path.exists()
            finally:
                # Restore original HOME
                if original_home:
                    os.environ["HOME"] = original_home
