"""Persist paper-trading JSON backup in the same GitHub repo (Streamlit Cloud)."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests

IST = ZoneInfo("Asia/Kolkata")

# Relative to test/paper-trade/ (committed to the monorepo).
DEFAULT_REPO_BACKUP_PATH = "test/paper-trade/data/paper_trading_cloud_backup.json"
LOCAL_BACKUP_FILENAME = "paper_trading_cloud_backup.json"


@dataclass(frozen=True)
class GitHubBackupConfig:
    token: str
    repository: str  # owner/repo
    branch: str
    repo_path: str

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.repository)


def _app_root() -> Path:
    return Path(__file__).resolve().parent.parent


def local_backup_path() -> Path:
    return _app_root() / "data" / LOCAL_BACKUP_FILENAME


def _read_secret(key: str, default: str = "") -> str:
    try:
        import streamlit as st

        val = st.secrets.get(key, default) or ""
        if val:
            return str(val).strip()
    except Exception:
        pass
    return str(os.environ.get(key, default) or "").strip()


def load_config() -> Optional[GitHubBackupConfig]:
    token = _read_secret("GITHUB_TOKEN") or _read_secret("PAPER_GITHUB_TOKEN")
    repo = _read_secret("GITHUB_REPOSITORY") or _read_secret("PAPER_GITHUB_REPOSITORY")
    if not token or not repo:
        return None
    if "/" not in repo:
        return None
    branch = _read_secret("GITHUB_BRANCH") or _read_secret("PAPER_GITHUB_BRANCH") or "main"
    repo_path = _read_secret("PAPER_BACKUP_REPO_PATH") or DEFAULT_REPO_BACKUP_PATH
    return GitHubBackupConfig(
        token=token,
        repository=repo.strip(),
        branch=branch.strip(),
        repo_path=repo_path.strip().lstrip("/"),
    )


def read_local_backup() -> Optional[dict[str, Any]]:
    path = local_backup_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def write_local_backup(data: dict[str, Any]) -> Path:
    path = local_backup_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def fetch_github_backup(cfg: GitHubBackupConfig) -> tuple[Optional[dict[str, Any]], str]:
    """Returns (payload, status_message)."""
    owner, repo = cfg.repository.split("/", 1)
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{cfg.repo_path}"
    headers = {
        "Authorization": f"Bearer {cfg.token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    params = {"ref": cfg.branch}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
    except requests.RequestException as exc:
        return None, f"GitHub read failed: {exc}"

    if resp.status_code == 404:
        return None, "No backup file on GitHub yet."
    if resp.status_code != 200:
        return None, f"GitHub read HTTP {resp.status_code}: {resp.text[:200]}"

    body = resp.json()
    content_b64 = body.get("content")
    if not content_b64:
        return None, "GitHub response missing content."
    try:
        raw = base64.b64decode(content_b64)
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        return None, f"Invalid backup JSON from GitHub: {exc}"
    if not isinstance(data, dict):
        return None, "Backup is not a JSON object."
    return data, "Loaded from GitHub."


def push_github_backup(cfg: GitHubBackupConfig, data: dict[str, Any]) -> str:
    owner, repo = cfg.repository.split("/", 1)
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{cfg.repo_path}"
    headers = {
        "Authorization": f"Bearer {cfg.token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload_bytes = json.dumps(data, indent=2).encode("utf-8")
    content_b64 = base64.b64encode(payload_bytes).decode("ascii")

    get_resp = requests.get(
        url,
        headers=headers,
        params={"ref": cfg.branch},
        timeout=30,
    )
    body: dict[str, Any] = {
        "message": f"paper-trading: backup {datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}",
        "content": content_b64,
        "branch": cfg.branch,
    }
    if get_resp.status_code == 200:
        sha = get_resp.json().get("sha")
        if sha:
            body["sha"] = sha

    try:
        put_resp = requests.put(url, headers=headers, json=body, timeout=45)
    except requests.RequestException as exc:
        return f"GitHub write failed: {exc}"

    if put_resp.status_code not in (200, 201):
        return f"GitHub write HTTP {put_resp.status_code}: {put_resp.text[:300]}"

    return f"Pushed to {cfg.repository}@{cfg.branch}:{cfg.repo_path}"


def best_available_backup(cfg: Optional[GitHubBackupConfig]) -> tuple[Optional[dict[str, Any]], str]:
    """Pick newest backup by exported_at from local file and GitHub."""
    candidates: list[tuple[str, dict[str, Any], str]] = []
    remote_msg = ""

    local = read_local_backup()
    if local:
        candidates.append((str(local.get("exported_at", "")), local, "local file"))

    if cfg and cfg.enabled:
        remote, remote_msg = fetch_github_backup(cfg)
        if remote:
            candidates.append((str(remote.get("exported_at", "")), remote, "GitHub API"))

    if not candidates:
        return None, remote_msg or "No backup file (local or GitHub)."
    _ts, data, src = max(candidates, key=lambda x: x[0])
    return data, f"Using {src} (exported_at={data.get('exported_at', '?')})"


def push_cloud_backup(data: dict[str, Any]) -> str:
    """Write local JSON and commit to GitHub when configured."""
    write_local_backup(data)
    cfg = load_config()
    if not cfg or not cfg.enabled:
        return "Saved locally only (set GITHUB_TOKEN + GITHUB_REPOSITORY in secrets to commit)."
    return push_github_backup(cfg, data)


def config_status_line() -> str:
    cfg = load_config()
    if cfg and cfg.enabled:
        return f"GitHub backup: **on** → `{cfg.repo_path}` on `{cfg.repository}` (`{cfg.branch}`)"
    return "GitHub backup: **off** (add secrets — see Settings or DEPLOY_PAPER_TRADING.md)"
