# スーパー耐久通知監視 BOT

GitHub Actions + Python + Playwright を使い、認証付きページの通知一覧を **10分おきに監視** し、変更があれば Slack に通知する自動監視 BOT です。

監視対象: `https://apps.mobilityland.co.jp/info/download/gDbFf5`

---

## 目次

1. [必要な GitHub Secrets](#必要な-github-secrets)
2. [初期セットアップ手順](#初期セットアップ手順)
3. [動作確認手順](#動作確認手順)
4. [セレクタの調整方法](#セレクタの調整方法)
5. [初回実行の挙動と切り替え方法](#初回実行の挙動と切り替え方法)
6. [state の仕組み](#state-の仕組み)
7. [エラー通知の抑制ルール](#エラー通知の抑制ルール)
8. [想定される壊れポイント](#想定される壊れポイント)
9. [GitHub Actions 無料枠について](#github-actions-無料枠について)
10. [将来の改善案](#将来の改善案)
11. [ファイル構成](#ファイル構成)

---

## 必要な GitHub Secrets

リポジトリの `Settings → Secrets and variables → Actions → New repository secret` で以下を登録してください。

| Secret 名 | 必須 | 説明 |
|-----------|------|------|
| `SLACK_WEBHOOK_URL` | **必須** | Slack Incoming Webhook URL。`https://hooks.slack.com/services/...` の形式。 |
| `TARGET_PASSWORD` | 任意 | 監視ページの認証パスワード。認証フォームがある場合のみ必要。 |
| `FIRST_RUN_NOTIFY` | 任意 | `true` にすると初回実行時も Slack 通知する (デフォルト: 通知しない)。 |

> **セキュリティ注意**: パスワードや Webhook URL はコードに直書きせず、必ず Secrets に登録してください。ログ・例外メッセージにも秘密値は出力しません。

---

## 初期セットアップ手順

### 1. リポジトリを用意する

```bash
# このディレクトリを git リポジトリとして初期化
cd super-taikyu-monitor
git init
git add .
git commit -m "initial commit"
```

**リポジトリの公開設定について:**
- **パブリック推奨**: GitHub Actions の無料分 (2000分/月) では 10分間隔の運用が困難です。パブリックリポジトリなら Actions 分は無制限です。通知内容に個人情報がなければパブリックで問題ありません。
- **プライベートの場合**: 60分間隔への変更か、GitHub Pro / 有料プランへのアップグレードを検討してください。

### 2. GitHub にプッシュする

```bash
# GitHub で空のリポジトリを作成してから
git remote add origin https://github.com/YOUR_USERNAME/super-taikyu-monitor.git
git branch -M main
git push -u origin main
```

### 3. GitHub Secrets を登録する

`Settings → Secrets and variables → Actions` で以下を登録:

- `SLACK_WEBHOOK_URL`: Slack の Incoming Webhook URL
- `TARGET_PASSWORD`: ページの認証パスワード (必要な場合)

### 4. Slack Incoming Webhook を取得する

1. [Slack API](https://api.slack.com/apps) → Create New App
2. Incoming Webhooks を有効化
3. "Add New Webhook to Workspace" → チャンネルを選択
4. Webhook URL をコピーして GitHub Secrets に登録

### 5. ローカルでの動作確認 (オプション)

```bash
# Python 仮想環境を作成
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 依存パッケージをインストール
pip install -r requirements.txt
playwright install chromium --with-deps

# .env ファイルを作成
cp .env.example .env
# .env を編集して実際の値を設定

# スクリプトを実行
python -m monitor.main
```

---

## 動作確認手順

### 1. 手動実行でテストする

GitHub Actions タブ → "通知監視 (スーパー耐久)" → "Run workflow" → "Run workflow"

ログを確認して以下を検証してください:

```
[INFO] ===== スーパー耐久通知監視 開始 =====
[INFO] state ファイルが存在しません。初回実行として扱います。
[INFO] https://apps.mobilityland.co.jp/... へのアクセスを開始します
[INFO] コンテナ検出: (セレクタ名)
[INFO] アイテムセレクタ: (セレクタ名) (N件)
[INFO] 取得件数: N件 / ハッシュ: sha256:...
[INFO] 基準値を保存しました。次回実行から差分比較を開始します。
[INFO] ===== スーパー耐久通知監視 正常終了 =====
```

### 2. 変更検知をテストする

```bash
# state ファイルを手動で編集して内容を変えてからコミットする
# または: state ファイルを削除して初回実行→再実行で差分を発生させる
```

### 3. セレクタが合っているか確認する

`LOG_LEVEL=DEBUG` で実行すると、セレクタの試行過程が詳細に表示されます:

1. GitHub Actions → Run workflow → `log_level` に `DEBUG` を入力
2. ログに以下が出ていれば正常:
   ```
   [DEBUG] セレクタマッチ: .notification-list
   ```
3. 以下が出ていればセレクタ調整が必要:
   ```
   [WARNING] 通知コンテナが見つかりません。フォールバック: ページ全体のリンクを収集します。
   ```

---

## セレクタの調整方法

セレクタが実サイトに合っていない場合は `monitor/config.py` を編集します。

### DOM 構造の確認方法

1. 対象ページをブラウザで開く
2. F12 → Elements タブ
3. 通知一覧の要素を右クリック → コピー → CSS セレクターをコピー
4. `config.py` の該当リストの **先頭** に追加する

### 調整箇所

```python
# monitor/config.py

NOTIFICATION_SELECTORS = {
    "container": [
        ".actual-container-class",  # ← 先頭に実際のセレクタを追加
        ".notification-list",       # 既存の候補 (順番に試される)
        ...
    ],
    "item": [...],
    "title": [...],
    "date": [...],
    "link": [...],
}
```

### parser_version の更新

セレクタ変更によって抽出結果が大きく変わる場合は、`config.py` の `PARSER_VERSION` をインクリメントしてください。
これにより、次回実行は「初回扱い」となり、偽の差分通知が送られません。

```python
PARSER_VERSION = 2  # 1 → 2 にインクリメント
```

---

## 初回実行の挙動と切り替え方法

### デフォルト動作

初回実行時は **基準値を保存するだけで Slack 通知しません**。
2回目以降から差分比較が始まります。

### 初回もSlack通知したい場合

GitHub Secrets で `FIRST_RUN_NOTIFY` を `true` に設定してください。

```
Secret名: FIRST_RUN_NOTIFY
値: true
```

初回実行後は `false` に戻すか削除することを推奨します (毎回通知されるわけではないですが明示的に管理するため)。

---

## state の仕組み

### 保存先

`state/monitor_state.json` にリポジトリへコミットして永続化します。

**なぜ git commit を選んだか:**
- artifact は TTL (90日) で削除される
- cache はブランチ間で共有されない / 消える可能性がある
- git commit は永続・無料・変更履歴が追跡できる
- 壊れても `git log` で復元できる

### state ファイルの構造

```json
{
  "schema_version": 1,
  "parser_version": 1,
  "source_url": "https://apps.mobilityland.co.jp/...",
  "fetched_at": "2024-01-15T15:30:00+09:00",
  "last_hash": "sha256:abc123...",
  "item_count": 5,
  "normalized_items": [
    {
      "title": "通知タイトル",
      "url": "https://example.com/doc.pdf",
      "date": "2024-01-10",
      "raw_text": "通知タイトル"
    }
  ],
  "error_state": {
    "consecutive_errors": 0,
    "last_error_type": null,
    "last_error_message": null,
    "last_error_notified_at": null
  }
}
```

### state をリセットしたい場合

```bash
# state ファイルを削除してコミット → 次回実行が初回扱いになる
rm state/monitor_state.json
git add state/
git commit -m "chore: state をリセット"
git push
```

---

## エラー通知の抑制ルール

連続エラー時に Slack に大量通知しないよう、以下の抑制ルールを設けています:

- **初回エラー**: 必ず通知
- **2回目以降の連続エラー**: 前回通知から **1時間** 経過後にのみ通知
- **成功時**: エラーカウントをリセット

抑制間隔の変更は `config.py` の `ERROR_NOTIFY_INTERVAL_SECONDS` を編集してください。

---

## 想定される壊れポイント

| ポイント | 原因 | 対処 |
|---------|------|------|
| セレクタが合わない | サイトのリニューアル | `config.py` のセレクタを更新 |
| 認証方式が変わる | サイト側の変更 | `browser.py` の `_authenticate()` を修正 |
| GitHub Actions の push 失敗 | 複数 workflow が同時実行 | ワークフローに concurrency 設定を追加 |
| Playwright のバージョン非互換 | 依存関係の更新 | `requirements.txt` のバージョンを固定 |
| state が破損 | 途中終了など | `state/monitor_state.json` を削除してリセット |
| Slack Webhook が無効化 | Slack アプリ設定変更 | Webhook URL を再取得して Secrets を更新 |
| networkidle にならない | SPA・非同期ロード | `config.py` の timeout を調整、wait selector を追加 |

---

## GitHub Actions 無料枠について

| リポジトリ | 毎月の無料分 | 10分間隔での推定使用量 | 判定 |
|-----------|------------|---------------------|------|
| **パブリック** | **無制限** | ― | **問題なし** |
| プライベート | 2,000分/月 | 約 8,640分/月 | **超過** |

**推奨: パブリックリポジトリで運用**

state ファイルに含まれるのは通知タイトル・URL・日付のみです。パスワードや Webhook URL は Secrets に保存されリポジトリには存在しません。

---

## 将来の改善案

1. **並行実行制御**: `concurrency:` グループを workflow に追加して、同時実行を防ぐ
2. **Slack Block Kit の強化**: より視認性の高いメッセージ形式に変更
3. **PDF 差分検知**: PDF のハッシュを取得してファイル差し替えを検知
4. **通知フィルタリング**: キーワードフィルタで特定種別の変更のみ通知
5. **Slack スレッド返信**: 同一変更に関する続報をスレッドで返信
6. **ヘルスチェック通知**: 週1回の「監視継続中」通知

---

## ファイル構成

```
super-taikyu-monitor/
├── .github/
│   └── workflows/
│       └── monitor.yml          # GitHub Actions (cron + 手動実行)
├── monitor/
│   ├── __init__.py
│   ├── config.py                # 設定・セレクタ定義 ← 調整はここ
│   ├── browser.py               # Playwright操作 (認証・取得)
│   ├── parser.py                # 正規化・ハッシュ・差分計算
│   ├── state.py                 # state JSON 管理
│   ├── slack.py                 # Slack 通知
│   └── main.py                  # エントリーポイント
├── state/
│   ├── .gitkeep                 # ディレクトリを git で追跡するためのファイル
│   └── monitor_state.json       # 実行後に自動生成・コミットされる
├── requirements.txt
├── .env.example                 # ローカル開発用の環境変数テンプレート
├── .gitignore
└── README.md
```
