import json
import warnings

import pyclaw.config as config_module


def test_a_leftover_rate_limit_is_flagged(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"rate_limit": {"agnes": 10}}))
    monkeypatch.setattr(config_module, "__config_file__", path)
    config_module.load.cache_clear()
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            config_module.load()
        assert any("rate_limit" in str(warning.message) for warning in caught)
    finally:
        config_module.load.cache_clear()


def test_a_config_without_rate_limit_is_quiet(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"model": "p/m"}))
    monkeypatch.setattr(config_module, "__config_file__", path)
    config_module.load.cache_clear()
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            config_module.load()
        assert not [warning for warning in caught if "rate_limit" in str(warning.message)]
    finally:
        config_module.load.cache_clear()
