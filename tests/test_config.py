import os
from pathlib import Path
from unittest.mock import patch
from src.config import Settings, load_settings


def test_settings_defaults():
    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=True):
        s = Settings()
        assert s.deepseek_model == "deepseek-v4-pro"
        assert s.session_max_turns == 200
        assert s.session_disk_limit_gb == 5


def test_resolve_paths(tmp_path):
    s = Settings(
        project_root=tmp_path,
        deepseek_api_key="test-key",
    )
    s.resolve_paths()
    assert s.report_dir.exists()
    assert s.session_dir.exists()
    assert s.database_path.parent.exists()
