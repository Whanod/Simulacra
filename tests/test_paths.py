from __future__ import annotations

from pathlib import Path

from defi_sim.paths import project_root, solana_plans_root


def test_project_root_prefers_explicit_runtime_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_root = tmp_path / "app"
    (runtime_root / "solana-plans").mkdir(parents=True)
    monkeypatch.setenv("DEFI_SIM_REPO_ROOT", str(runtime_root))

    assert project_root() == runtime_root
    assert solana_plans_root() == runtime_root / "solana-plans"


def test_project_root_discovers_solana_plans_from_cwd(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_root = tmp_path / "repo"
    nested = repo_root / "frontend"
    nested.mkdir(parents=True)
    (repo_root / "solana-plans").mkdir()
    monkeypatch.delenv("DEFI_SIM_REPO_ROOT", raising=False)
    monkeypatch.chdir(nested)

    assert project_root() == repo_root
