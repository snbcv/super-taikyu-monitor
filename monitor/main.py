"""
main.py

監視フローのエントリーポイント。
各モジュールを組み合わせて、取得→比較→通知→保存を行う。

実行方法:
  python -m monitor.main
"""
import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .browser import (
    AuthenticationError,
    BrowserError,
    ContentExtractionError,
    PageLoadError,
    fetch_notifications,
)
from .config import (
    ERROR_NOTIFY_INTERVAL_SECONDS,
    MONITOR_NAME,
    STATE_FILE_PATH,
    TARGET_URL,
)
from .parser import compute_diff, compute_hash, normalize_items
from .slack import (
    send_change_notification,
    send_error_notification,
    send_initial_run_notification,
)
from .state import (
    get_error_state,
    increment_error_state,
    is_first_run,
    load_state,
    needs_reset_due_to_version,
    reset_error_state,
    save_error_state_only,
    save_state,
    should_notify_error,
)

# ============================================================
# ログ設定
# ============================================================

def _setup_logging() -> None:
    """構造化ログを設定する (GitHub Actions のログ確認を意識)"""
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


# ============================================================
# メインフロー
# ============================================================

async def _run() -> int:
    """
    監視フローを実行し、終了コードを返す。

    Returns
    -------
    0 : 正常終了 (変更なし / 変更あり通知済み / 初回実行)
    1 : エラー終了
    """
    logger = logging.getLogger("monitor.main")

    # 環境変数の読み込み (.env は開発時のみ使用; CI では GitHub Secrets から注入)
    load_dotenv()

    target_password   = os.getenv("TARGET_PASSWORD")       # 認証パスワード (必須でない場合あり)
    slack_webhook_url = os.getenv("SLACK_WEBHOOK_URL")     # Slack Webhook URL (必須)
    first_run_notify  = os.getenv("FIRST_RUN_NOTIFY", "false").lower() == "true"

    # --- Slack Webhook URL 必須チェック ---
    if not slack_webhook_url:
        logger.critical(
            "SLACK_WEBHOOK_URL が設定されていません。"
            "GitHub Secrets に SLACK_WEBHOOK_URL を登録してください。"
        )
        return 1

    # ---- ① state 読み込み ----
    current_state = None
    try:
        current_state = load_state(STATE_FILE_PATH)
    except ValueError as exc:
        logger.error("state 読み込みエラー: %s", exc)
        logger.warning("state が破損しているため、初回実行として扱います。")
        current_state = None

    error_state = get_error_state(current_state)
    first_run   = is_first_run(current_state)
    version_reset = (not first_run) and needs_reset_due_to_version(current_state)

    if first_run:
        logger.info("=== 初回実行: 基準値を保存します ===")
    elif version_reset:
        logger.info("=== parser_version 変更: 今回は基準値を更新します ===")
    else:
        old_items = current_state.get("normalized_items", [])
        logger.info("前回取得件数: %d件", len(old_items))

    # ---- ② ページから通知を取得 ----
    try:
        logger.info("%s へのアクセスを開始します", TARGET_URL)
        raw_items = await fetch_notifications(url=TARGET_URL, password=target_password)

    except AuthenticationError as exc:
        error_msg = f"認証失敗: {exc}"
        logger.error(error_msg)
        return await _handle_error(
            "AuthenticationError", error_msg,
            error_state, current_state, slack_webhook_url,
        )

    except PageLoadError as exc:
        error_msg = f"ページ読み込み失敗: {exc}"
        logger.error(error_msg)
        return await _handle_error(
            "PageLoadError", error_msg,
            error_state, current_state, slack_webhook_url,
        )

    except ContentExtractionError as exc:
        error_msg = f"コンテンツ取得失敗: {exc}"
        logger.error(error_msg)
        return await _handle_error(
            "ContentExtractionError", error_msg,
            error_state, current_state, slack_webhook_url,
        )

    except BrowserError as exc:
        error_msg = f"ブラウザエラー: {exc}"
        logger.error(error_msg)
        return await _handle_error(
            "BrowserError", error_msg,
            error_state, current_state, slack_webhook_url,
        )

    except Exception as exc:
        error_msg = f"予期しないエラー: {type(exc).__name__}: {exc}"
        logger.exception("予期しないエラーが発生しました")
        return await _handle_error(
            "UnexpectedError", error_msg,
            error_state, current_state, slack_webhook_url,
        )

    # ---- ③ 正規化 ----
    new_items   = normalize_items(raw_items)
    new_hash    = compute_hash(new_items)
    logger.info("取得件数: %d件 / ハッシュ: %s", len(new_items), new_hash)

    # ---- ④ 初回実行 or バージョン変更 → 基準値を保存して終了 ----
    if first_run or version_reset:
        save_state(
            normalized_items=new_items,
            last_hash=new_hash,
            error_state=reset_error_state(),
        )
        logger.info("基準値を保存しました。次回実行から差分比較を開始します。")

        if first_run_notify and slack_webhook_url:
            logger.info("FIRST_RUN_NOTIFY=true: 初回実行通知を送信します。")
            send_initial_run_notification(
                webhook_url=slack_webhook_url,
                item_count=len(new_items),
            )

        return 0

    # ---- ⑤ 差分比較 ----
    old_items    = current_state.get("normalized_items", [])
    old_hash     = current_state.get("last_hash", "")

    if new_hash == old_hash:
        logger.info("変更なし (ハッシュ一致)。Slack 通知はスキップします。")
        # 成功実行なのでエラー状態をリセット
        save_state(
            normalized_items=new_items,
            last_hash=new_hash,
            error_state=reset_error_state(),
        )
        return 0

    # ハッシュが異なる → 詳細差分を計算
    diff = compute_diff(old_items, new_items)

    if not diff["has_diff"]:
        # ハッシュは違うが実質的な差分なし (正規化の揺れによるノイズ)
        logger.info("実質的な差分なし (正規化後に差分ゼロ)。ハッシュのみ更新します。")
        save_state(
            normalized_items=new_items,
            last_hash=new_hash,
            error_state=reset_error_state(),
        )
        return 0

    # ---- ⑥ 差分あり → Slack 通知 ----
    logger.info(
        "差分を検知: 追加=%d 削除=%d 更新=%d",
        len(diff["added"]),
        len(diff["removed"]),
        len(diff["modified"]),
    )

    notify_success = send_change_notification(
        webhook_url=slack_webhook_url,
        diff=diff,
        source_url=TARGET_URL,
    )

    if not notify_success:
        logger.warning("Slack 通知の送信に失敗しましたが、state は更新します。")

    # ---- ⑦ state を更新して保存 ----
    save_state(
        normalized_items=new_items,
        last_hash=new_hash,
        error_state=reset_error_state(),
    )

    return 0


async def _handle_error(
    error_type: str,
    error_message: str,
    error_state: dict,
    current_state,
    slack_webhook_url: str,
) -> int:
    """
    エラー発生時の共通処理。
    エラー状態を更新し、抑制ルールに従って Slack に通知する。
    """
    logger = logging.getLogger("monitor.main.error")

    new_error_state_no_notify = increment_error_state(
        error_state=error_state,
        error_type=error_type,
        error_message=error_message,
        notified=False,
    )
    consecutive = new_error_state_no_notify["consecutive_errors"]

    do_notify = should_notify_error(new_error_state_no_notify, ERROR_NOTIFY_INTERVAL_SECONDS)

    if do_notify:
        logger.info(
            "エラー通知を送信します (連続 %d 回目)",
            consecutive,
        )
        notified = send_error_notification(
            webhook_url=slack_webhook_url,
            error_type=error_type,
            error_message=error_message,
            consecutive_count=consecutive,
        )
    else:
        logger.info(
            "エラー通知を抑制します (連続 %d 回目 / 前回通知から %d秒以内)",
            consecutive,
            ERROR_NOTIFY_INTERVAL_SECONDS,
        )
        notified = False

    final_error_state = increment_error_state(
        error_state=error_state,
        error_type=error_type,
        error_message=error_message,
        notified=notified,
    )

    save_error_state_only(final_error_state)
    return 1


# ============================================================
# エントリーポイント
# ============================================================

def main() -> int:
    _setup_logging()
    logger = logging.getLogger("monitor.main")
    logger.info("===== %s 開始 =====", MONITOR_NAME)

    try:
        exit_code = asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("中断されました。")
        exit_code = 0
    except Exception as exc:
        logger.exception("main() で予期しない例外: %s", exc)
        exit_code = 1

    status = "正常終了" if exit_code == 0 else "エラー終了"
    logger.info("===== %s %s (exit=%d) =====", MONITOR_NAME, status, exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
