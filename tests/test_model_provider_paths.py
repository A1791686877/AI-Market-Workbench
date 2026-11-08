"""Codex CLI path resolution: a self-update must not silently break the chain or burn the budget."""
from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

import pytest

from app import model_provider as provider_module
from app.providers import ProviderConfigurationError  # noqa: F401  (imported for symmetry)
from app.model_provider import ProviderError, resolve_codex_executable


def _make_binary(root: Path, name: str, *, mtime: float | None = None) -> Path:
    directory = root / "bin" / name
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "codex.exe"
    path.write_bytes(b"stub")
    if mtime is not None:
        import os
        os.utime(path, (mtime, mtime))
    return path


def test_stale_bin_hash_resolves_to_the_newest_sibling(monkeypatch, tmp_path) -> None:
    """The configured bin/<hash> was replaced by a self-update: fall back to the newest sibling."""
    stamp = time.time()
    _make_binary(tmp_path, "oldhash", mtime=stamp - 3600)
    newest = _make_binary(tmp_path, "newhash", mtime=stamp)
    configured = tmp_path / "bin" / "gonehash" / "codex.exe"        # what AMW_CODEX_COMMAND still points at
    monkeypatch.setattr(provider_module, "settings", replace(
        provider_module.settings, codex_command=str(configured)))
    resolved, tried = resolve_codex_executable()
    assert Path(resolved) == newest
    assert str(configured) in tried
    assert str(newest) in tried


def test_absolute_existing_path_wins(monkeypatch, tmp_path) -> None:
    configured = _make_binary(tmp_path, "onlyhash")
    monkeypatch.setattr(provider_module, "settings", replace(
        provider_module.settings, codex_command=str(configured)))
    resolved, tried = resolve_codex_executable()
    assert Path(resolved) == configured
    assert tried == [str(configured)]


def test_nothing_resolvable_fails_closed_with_the_paths_tried(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(provider_module, "settings", replace(
        provider_module.settings, codex_command=str(tmp_path / "bin" / "gone" / "codex.exe")))
    with pytest.raises(ProviderError) as excinfo:
        resolve_codex_executable()
    assert "AMW_CODEX_COMMAND" in str(excinfo.value)


def test_resolution_happens_before_the_budget_check(monkeypatch, tmp_path) -> None:
    """A stale path is a configuration error: it must not consume a 24h model attempt."""
    from app import outlook_registry  # noqa: F401  (ensures app import order is stable)

    calls: list[str] = []

    def fake_invoke(*args, **kwargs):  # pragma: no cover - should never run
        calls.append("invoke")
        raise AssertionError("the model path must not be reached")

    monkeypatch.setattr(provider_module, "settings", replace(
        provider_module.settings, codex_command=str(tmp_path / "bin" / "gone" / "codex.exe"),
        data_mode="live", codex_default_model="test-model"))
    monkeypatch.setattr(provider_module, "_invoke", fake_invoke)
    with pytest.raises(ProviderError):
        provider_module.invoke(snapshot=None, model="test-model", prompt_version="scenario-v2")
    assert calls == []
