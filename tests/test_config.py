"""配置变量应使用项目命名空间，避免被 shell/IDE 的通用变量污染。"""
from app.core.config import Settings


def test_generic_debug_environment_variable_is_ignored(monkeypatch):
    monkeypatch.setenv("DEBUG", "release")
    monkeypatch.delenv("SUPPORTFLOW_DEBUG", raising=False)
    assert Settings(_env_file=None).debug is True


def test_namespaced_debug_environment_variable_is_supported(monkeypatch):
    monkeypatch.setenv("DEBUG", "release")
    monkeypatch.setenv("SUPPORTFLOW_DEBUG", "false")
    assert Settings(_env_file=None).debug is False
