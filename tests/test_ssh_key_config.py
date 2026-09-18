import importlib


def test_ssh_key_has_no_repository_default(monkeypatch):
    monkeypatch.delenv("SSH_KEY_PATH", raising=False)
    import config
    importlib.reload(config)
    assert config.SSH_KEY_PATH == ""
