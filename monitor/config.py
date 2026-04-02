"""
設定ファイル

セレクタを変更したい場合は NOTIFICATION_SELECTORS / AUTH_SELECTORS を編集してください。
その他の動作変更もこのファイルに集約しています。
"""
from pathlib import Path

# ============================================================
# 基本設定
# ============================================================
TARGET_URL = "https://apps.mobilityland.co.jp/info/download/gDbFf5"
MONITOR_NAME = "スーパー耐久通知監視"
STATE_FILE_PATH = Path("state/monitor_state.json")

# スキーマバージョン: state JSON の構造自体を変えた場合にインクリメント
STATE_SCHEMA_VERSION = 1

# パーサーバージョン: 取得・解析ロジックを変えた場合にインクリメント
# バージョンが state の値と異なる場合は "初回実行" 扱いとなり、差分通知しない
PARSER_VERSION = 1

# ============================================================
# タイムアウト・リトライ設定
# ============================================================
PAGE_LOAD_TIMEOUT_MS = 30_000    # ページ読み込みタイムアウト (30秒)
ELEMENT_TIMEOUT_MS   = 10_000    # 要素待機タイムアウト (10秒)
NETWORK_IDLE_TIMEOUT_MS = 5_000  # networkidle 待機タイムアウト
MAX_RETRIES          = 3         # 最大リトライ回数
RETRY_WAIT_SECONDS   = 10        # リトライ間隔 (秒)

# ============================================================
# エラー抑制設定
# ============================================================
# 連続エラー時に Slack 再通知するまでの最低間隔 (秒)
# デフォルト: 1時間 = 3600秒
ERROR_NOTIFY_INTERVAL_SECONDS = 3_600

# ============================================================
# User-Agent
# ============================================================
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ============================================================
# セレクタ設定
#
# !! 以下は実サイトを確認してから調整が必要です !!
#
# 調整方法:
#   1. ブラウザで対象ページを開く
#   2. 開発者ツール (F12) → Elements タブ
#   3. 対象要素を右クリック → コピー → CSSセレクターをコピー
#   4. 下記リストの先頭に追加する
#
# 選択ロジック:
#   各リストは「先頭から順に試し、最初にマッチしたものを使用」
# ============================================================

# [要確認] 認証フォームのセレクタ候補
AUTH_SELECTORS = {
    # パスワード入力フィールド候補
    "password_input": [
        'input[type="password"]',
        'input[name="password"]',
        'input[name="pass"]',
        'input[id="password"]',
        'input[id="pass"]',
        "#password",
        ".password",
    ],
    # 送信ボタン候補
    "submit_button": [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("ログイン")',
        'button:has-text("送信")',
        'button:has-text("確認")',
        'button:has-text("入力")',
        ".btn-submit",
        ".submit",
        "#submit",
    ],
    # 認証成功の確認用セレクタ候補 (これらのいずれかが表示されれば認証成功とみなす)
    "post_auth_indicator": [
        ".notification-list",
        ".info-list",
        ".download-list",
        ".news-list",
        ".content",
        "table",
        "main article",
        "#content",
        "article",
    ],
}

# [要確認] 通知一覧のセレクタ候補
NOTIFICATION_SELECTORS = {
    # 通知一覧コンテナ候補 (先頭から順に試す)
    "container": [
        ".notification-list",
        ".info-list",
        ".download-list",
        ".news-list",
        ".list",
        "table",
        "ul",
        ".content",
        "main",
        "article",
    ],
    # 個別通知アイテム候補 (コンテナ内で繰り返す要素)
    "item": [
        "li",
        "tr",
        ".item",
        ".entry",
        ".row",
        "article",
        "section",
    ],
    # タイトル候補 (アイテム内のメインテキスト)
    "title": [
        "a",
        ".title",
        "h3",
        "h4",
        "td:nth-child(2)",
        "td:first-child",
        "p",
    ],
    # 日付候補
    "date": [
        ".date",
        "time",
        "td.date",
        "span.date",
        ".published",
        ".datetime",
        "td:first-child",
        ".time",
    ],
    # リンク候補
    "link": [
        'a[href]',
        'a[href$=".pdf"]',
        'a[href*="download"]',
        'a[href*="file"]',
    ],
}

# 通知に含めないとみなすセレクタ (ヘッダー・フッター・ナビゲーション等)
SKIP_ITEM_SELECTORS = [
    "header",
    "footer",
    "nav",
    ".breadcrumb",
    ".pagination",
]

# URL正規化時に除去するクエリパラメータ (トラッキング等)
URL_STRIP_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_eid", "_ga", "ref",
}
