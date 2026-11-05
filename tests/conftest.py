import pytest


@pytest.fixture(autouse=True)
def _no_personal_config(tmp_path, monkeypatch):
    """手元の ~/.ai-office/config.json をテストに混ぜない。"""
    monkeypatch.setenv("AI_OFFICE_LOCAL_CONFIG", str(tmp_path / "no-personal-config.json"))
    monkeypatch.delenv("AI_OFFICE_WORKDIR", raising=False)
