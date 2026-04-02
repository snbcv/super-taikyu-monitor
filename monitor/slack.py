"""
slack.py

Slack Incoming Webhook への通知送信を担当。
通常の変更通知とエラー通知を別関数で管理し、
メッセージが長い場合は省略して見やすく整形する。
"""
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

from .config import MONITOR_NAME, TARGET_URL

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))

# ============================================================
# 定数
# ============================================================
MAX_ITEMS_IN_MESSAGE = 10      # 1通知に含める最大アイテム数
MAX_TEXT_LENGTH      = 200     # アイテムタイトルの最大文字数 (超過は省略)
SLACK_TIMEOUT_SECONDS = 10    # Slack HTTP タイムアウト


# ============================================================
# 内部ユーティリティ
# ============================================================

def _now_jst_str() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST")


def _truncate(text: Optional[str], max_len: int = MAX_TEXT_LENGTH) -> str:
    if not text:
        return "(タイトルなし)"
    if len(text) > max_len:
        return text[:max_len] + "…"
    return text


def _format_item(item: dict) -> str:
    """アイテム1件を1行のテキストにまとめる"""
    title = _truncate(item.get("title"))
    url   = item.get("url")
    date  = item.get("date") or ""
    date_str = f"[{date}] " if date else ""

    if url:
        return f"{date_str}<{url}|{title}>"
    return f"{date_str}{title}"


def _post_to_slack(webhook_url: str, payload: dict) -> bool:
    """
    Slack Webhook に JSON ペイロードを POST する。
    成功なら True、失敗なら False を返す (例外は送出しない)。
    """
    try:
        resp = requests.post(
            webhook_url,
            json=payload,
            timeout=SLACK_TIMEOUT_SECONDS,
        )
        if resp.status_code != 200:
            logger.error(
                "Slack 送信失敗: HTTP %s, レスポンス: %s",
                resp.status_code,
                resp.text[:200],
            )
            return False
        logger.info("Slack 通知送信成功")
        return True
    except requests.exceptions.Timeout:
        logger.error("Slack 送信タイムアウト (%d秒)", SLACK_TIMEOUT_SECONDS)
        return False
    except requests.exceptions.RequestException as exc:
        logger.error("Slack 送信エラー: %s", exc)
        return False


# ============================================================
# 変更通知
# ============================================================

def send_change_notification(
    webhook_url: str,
    diff: dict,
    source_url: str = TARGET_URL,
) -> bool:
    """
    通知内容に変更があったときの Slack 通知を送る。

    Parameters
    ----------
    webhook_url : Slack Incoming Webhook URL
    diff        : parser.compute_diff() の戻り値
    source_url  : 監視対象URL

    Returns
    -------
    bool : 送信成功なら True
    """
    added    = diff.get("added",    [])
    removed  = diff.get("removed",  [])
    modified = diff.get("modified", [])

    now_str  = _now_jst_str()

    # ヘッダーブロック
    summary_lines = [
        f"*[{MONITOR_NAME}] 変更を検知しました*",
        f"検知時刻: {now_str}",
        f"監視URL: {source_url}",
        "",
        f"追加: *{len(added)}件* ／ 削除: *{len(removed)}件* ／ 更新: *{len(modified)}件*",
    ]

    # 追加アイテム
    detail_lines = []
    if added:
        detail_lines.append("\n*追加された項目:*")
        for item in added[:MAX_ITEMS_IN_MESSAGE]:
            detail_lines.append(f"• {_format_item(item)}")
        if len(added) > MAX_ITEMS_IN_MESSAGE:
            detail_lines.append(f"  _(他 {len(added) - MAX_ITEMS_IN_MESSAGE} 件)_")

    # 削除アイテム
    if removed:
        detail_lines.append("\n*削除された項目:*")
        for item in removed[:MAX_ITEMS_IN_MESSAGE]:
            detail_lines.append(f"• {_format_item(item)}")
        if len(removed) > MAX_ITEMS_IN_MESSAGE:
            detail_lines.append(f"  _(他 {len(removed) - MAX_ITEMS_IN_MESSAGE} 件)_")

    # 更新アイテム
    if modified:
        detail_lines.append("\n*更新された項目:*")
        for entry in modified[:MAX_ITEMS_IN_MESSAGE]:
            old_item = entry.get("old", {})
            new_item = entry.get("new", {})
            old_title = _truncate(old_item.get("title"), 80)
            new_title = _truncate(new_item.get("title"), 80)

            if old_item.get("title") != new_item.get("title"):
                detail_lines.append(f"• タイトル変更:\n  旧: {old_title}\n  新: {new_title}")
            elif old_item.get("date") != new_item.get("date"):
                detail_lines.append(
                    f"• 日付変更: {_truncate(new_item.get('title'), 60)}"
                    f" ({old_item.get('date')} → {new_item.get('date')})"
                )
            else:
                detail_lines.append(f"• {_format_item(new_item)}")

        if len(modified) > MAX_ITEMS_IN_MESSAGE:
            detail_lines.append(f"  _(他 {len(modified) - MAX_ITEMS_IN_MESSAGE} 件)_")

    full_text = "\n".join(summary_lines + detail_lines)

    # Slack のメッセージ上限は 3000 文字 (mrkdwn ブロック)
    if len(full_text) > 2800:
        full_text = full_text[:2800] + "\n_(メッセージが長すぎるため省略しました)_"

    payload = {
        "text": f"[{MONITOR_NAME}] 変更を検知しました ({now_str})",  # 通知プッシュ用プレーンテキスト
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": full_text,
                },
            },
            {
                "type": "divider",
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"監視BOT: {MONITOR_NAME} | {now_str}",
                    }
                ],
            },
        ],
    }

    return _post_to_slack(webhook_url, payload)


# ============================================================
# エラー通知
# ============================================================

def send_error_notification(
    webhook_url: str,
    error_type: str,
    error_message: str,
    consecutive_count: int,
    source_url: str = TARGET_URL,
) -> bool:
    """
    エラーが発生したときの Slack 通知を送る。

    Parameters
    ----------
    webhook_url       : Slack Incoming Webhook URL
    error_type        : エラーの種別 (例: "PageLoadError")
    error_message     : エラーの詳細 (秘密情報を含めないこと)
    consecutive_count : 連続エラー回数
    source_url        : 監視対象URL

    Returns
    -------
    bool : 送信成功なら True
    """
    now_str = _now_jst_str()

    text = "\n".join([
        f":warning: *[{MONITOR_NAME}] エラーが発生しました*",
        f"時刻: {now_str}",
        f"監視URL: {source_url}",
        "",
        f"エラー種別: `{error_type}`",
        f"連続エラー回数: {consecutive_count}回",
        "",
        f"詳細: {_truncate(error_message, 300)}",
        "",
        "_次回実行は約10分後です。エラーが続く場合はログを確認してください。_",
    ])

    payload = {
        "text": f"[{MONITOR_NAME}] エラー: {error_type} ({now_str})",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": text,
                },
            },
        ],
    }

    return _post_to_slack(webhook_url, payload)


# ============================================================
# 初回実行通知 (オプション)
# ============================================================

def send_initial_run_notification(
    webhook_url: str,
    item_count: int,
    source_url: str = TARGET_URL,
) -> bool:
    """
    初回実行時 (基準値保存のみ) に Slack へ通知する。
    FIRST_RUN_NOTIFY=true のときのみ呼ばれる。
    """
    now_str = _now_jst_str()

    text = "\n".join([
        f":mag: *[{MONITOR_NAME}] 初回実行完了 (基準値を保存しました)*",
        f"時刻: {now_str}",
        f"監視URL: {source_url}",
        f"取得件数: {item_count}件",
        "",
        "_次回以降の実行で変更が検知された場合に通知します。_",
    ])

    payload = {
        "text": f"[{MONITOR_NAME}] 初回実行完了 ({now_str})",
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": text},
            }
        ],
    }

    return _post_to_slack(webhook_url, payload)
