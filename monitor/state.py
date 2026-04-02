"""
state.py

監視状態 (最終取得内容・ハッシュ・エラー状態) の読み書きを担当。
state は JSON ファイルとしてリポジトリに保存し、git commit で永続化する。

スキーマ例:
{
  "schema_version": 1,
  "parser_version": 1,
  "source_url": "https://...",
  "fetched_at": "2024-01-15T15:30:00+09:00",
  "last_hash": "sha256:abc123...",
  "item_count": 5,
  "normalized_items": [...],
  "error_state": {
    "consecutive_errors": 0,
    "last_error_type": null,
    "last_error_message": null,
    "last_error_notified_at": null
  }
}
"""
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from .config import (
    PARSER_VERSION,
    STATE_FILE_PATH,
    STATE_SCHEMA_VERSION,
    TARGET_URL,
)

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))

# ============================================================
# デフォルトエラー状態
# ============================================================

def _default_error_state() -> dict:
    return {
        "consecutive_errors":     0,
        "last_error_type":        None,
        "last_error_message":     None,
        "last_error_notified_at": None,
    }


# ============================================================
# state 読み込み
# ============================================================

def load_state(path: Path = STATE_FILE_PATH) -> Optional[dict]:
    """
    state JSON を読み込む。

    Returns
    -------
    dict  : 読み込んだ state
    None  : ファイルが存在しない (= 初回実行)

    Raises
    ------
    ValueError : JSON が破損している場合 (呼び出し元でハンドリング)
    """
    if not path.exists():
        logger.info("state ファイルが存在しません。初回実行として扱います。(%s)", path)
        return None

    try:
        with path.open("r", encoding="utf-8") as f:
            state = json.load(f)
        logger.info("state 読み込み完了: %s", path)
        return state
    except json.JSONDecodeError as exc:
        raise ValueError(f"state ファイルが破損しています ({path}): {exc}") from exc
    except OSError as exc:
        raise ValueError(f"state ファイルの読み込み失敗 ({path}): {exc}") from exc


# ============================================================
# state 書き込み
# ============================================================

def save_state(
    normalized_items: list[dict],
    last_hash: str,
    error_state: Optional[dict] = None,
    path: Path = STATE_FILE_PATH,
) -> None:
    """
    state JSON を書き込む。ディレクトリがなければ作成する。
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    now_jst = datetime.now(JST).isoformat()

    state = {
        "schema_version":   STATE_SCHEMA_VERSION,
        "parser_version":   PARSER_VERSION,
        "source_url":       TARGET_URL,
        "fetched_at":       now_jst,
        "last_hash":        last_hash,
        "item_count":       len(normalized_items),
        "normalized_items": normalized_items,
        "error_state":      error_state or _default_error_state(),
    }

    try:
        with path.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        logger.info("state 保存完了: %s", path)
    except OSError as exc:
        # 保存失敗は運用上致命的なので例外を伝搬する
        raise RuntimeError(f"state の保存失敗 ({path}): {exc}") from exc


# ============================================================
# 状態チェック
# ============================================================

def is_first_run(state: Optional[dict]) -> bool:
    """state が None (= ファイルなし) なら初回実行"""
    return state is None


def needs_reset_due_to_version(state: dict) -> bool:
    """
    parser_version が変わった場合は差分比較せず初回扱いにする。

    理由: パーサーの変更で正規化結果が変わると、
    実際には何も変わっていないのに大量の「差分」が出てしまうのを防ぐ。
    """
    if state.get("parser_version") != PARSER_VERSION:
        logger.warning(
            "parser_version が変わりました (state: %s → current: %s)。"
            "今回は初回実行として扱い、差分通知しません。",
            state.get("parser_version"),
            PARSER_VERSION,
        )
        return True
    return False


# ============================================================
# エラー状態管理
# ============================================================

def get_error_state(state: Optional[dict]) -> dict:
    """state からエラー状態を取得。なければデフォルトを返す"""
    if state is None:
        return _default_error_state()
    return state.get("error_state") or _default_error_state()


def increment_error_state(
    error_state: dict,
    error_type: str,
    error_message: str,
    notified: bool,
) -> dict:
    """エラー発生時にエラー状態を更新する"""
    now_jst = datetime.now(JST).isoformat()
    return {
        "consecutive_errors":     error_state.get("consecutive_errors", 0) + 1,
        "last_error_type":        error_type,
        "last_error_message":     error_message,
        "last_error_notified_at": now_jst if notified else error_state.get("last_error_notified_at"),
    }


def reset_error_state() -> dict:
    """成功時にエラー状態をリセット"""
    return _default_error_state()


def should_notify_error(error_state: dict, interval_seconds: int) -> bool:
    """
    エラーを Slack 通知すべきか判断する (連続エラーの大量通知を防ぐ)

    初回エラー、または前回通知から interval_seconds 以上経過していれば通知する。
    """
    if error_state.get("consecutive_errors", 0) == 1:
        return True  # 初回エラーは必ず通知

    last_notified_str = error_state.get("last_error_notified_at")
    if not last_notified_str:
        return True

    try:
        last_notified = datetime.fromisoformat(last_notified_str)
        elapsed = (datetime.now(JST) - last_notified).total_seconds()
        return elapsed >= interval_seconds
    except Exception:
        return True  # パース失敗なら通知する


def save_error_state_only(error_state: dict, path: Path = STATE_FILE_PATH) -> None:
    """
    エラー発生時に既存 state のエラー状態だけ更新して保存する。
    通常コンテンツは変更しない。
    """
    existing = None
    try:
        existing = load_state(path)
    except Exception:
        pass

    if existing is None:
        # 初回実行でエラーが起きた場合 (state がまだ存在しない)
        path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "schema_version":   STATE_SCHEMA_VERSION,
            "parser_version":   PARSER_VERSION,
            "source_url":       TARGET_URL,
            "fetched_at":       datetime.now(JST).isoformat(),
            "last_hash":        "",
            "item_count":       0,
            "normalized_items": [],
            "error_state":      error_state,
        }
    else:
        existing["error_state"] = error_state

    try:
        with path.open("w", encoding="utf-8") as f:
            json.dump(existing if existing else state, f, ensure_ascii=False, indent=2)
        logger.info("エラー状態を保存しました: %s", path)
    except OSError as exc:
        logger.error("エラー状態の保存失敗: %s", exc)
