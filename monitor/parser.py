"""
parser.py

取得した通知アイテムの正規化・ハッシュ計算・差分判定を担当。
browser.py とは分離し、純粋な変換・比較ロジックのみを持つ。
"""
import hashlib
import json
import logging
import re
import unicodedata
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from .config import URL_STRIP_PARAMS

logger = logging.getLogger(__name__)


# ============================================================
# テキスト正規化
# ============================================================

def normalize_text(text: Optional[str]) -> str:
    """
    テキストを比較用に正規化する。

    処理内容:
      - None → 空文字
      - Unicode 正規化 (NFKC: 全角英数→半角、結合文字処理など)
      - 制御文字・ゼロ幅文字を除去
      - 改行・タブ→半角スペース
      - 連続する空白を1つに
      - 前後の空白を除去
    """
    if not text:
        return ""

    # Unicode 正規化
    text = unicodedata.normalize("NFKC", text)

    # 制御文字・ゼロ幅文字を除去 (印刷可能文字と空白のみ残す)
    text = re.sub(r"[\u200b\u200c\u200d\ufeff\u00ad]", "", text)
    text = re.sub(r"[\r\n\t]", " ", text)
    text = re.sub(r" {2,}", " ", text)

    return text.strip()


# ============================================================
# URL 正規化
# ============================================================

def normalize_url(url: Optional[str]) -> Optional[str]:
    """
    URL を比較用に正規化する。

    処理内容:
      - None → None
      - トラッキングパラメータを除去
      - クエリパラメータをアルファベット順にソート (差分ノイズ削減)
      - フラグメント (#anchor) を除去
      - 末尾スラッシュを統一 (パス部分の末尾スラッシュは除去)
    """
    if not url:
        return None

    try:
        parsed = urlparse(url.strip())

        # クエリパラメータをフィルタリング&ソート
        query_params = parse_qs(parsed.query, keep_blank_values=True)
        filtered_params = {
            k: v for k, v in query_params.items()
            if k not in URL_STRIP_PARAMS
        }
        # アルファベット順にソート
        sorted_query = urlencode(
            sorted(filtered_params.items()), doseq=True
        )

        # パスの末尾スラッシュを除去 (ルートパス "/" は除外)
        path = parsed.path
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")

        normalized = urlunparse((
            parsed.scheme,
            parsed.netloc.lower(),
            path,
            parsed.params,
            sorted_query,
            "",  # フラグメントを除去
        ))
        return normalized

    except Exception as exc:
        logger.debug("URL正規化失敗 (%s): %s", url, exc)
        return url


# ============================================================
# アイテム正規化
# ============================================================

def normalize_item(raw_item: dict) -> dict:
    """
    browser.py から受け取った生アイテムを比較用に正規化する。

    Returns
    -------
    dict:
        title     : 正規化済みタイトル
        url       : 正規化済みURL (None の場合あり)
        date      : 正規化済み日付文字列 (None の場合あり)
        raw_text  : title + date の結合テキスト (比較の主キー)
    """
    title = normalize_text(raw_item.get("title"))
    url   = normalize_url(raw_item.get("url"))
    date  = normalize_text(raw_item.get("date"))

    # 比較キー: title が空なら url を使う
    raw_text = title if title else (url or "")

    return {
        "title":    title,
        "url":      url,
        "date":     date,
        "raw_text": raw_text,
    }


def normalize_items(raw_items: list[dict]) -> list[dict]:
    """raw_items 全体を正規化し、raw_text が空のものを除外する"""
    normalized = []
    for item in raw_items:
        n = normalize_item(item)
        if n["raw_text"]:
            normalized.append(n)
    return normalized


# ============================================================
# ハッシュ計算
# ============================================================

def compute_hash(items: list[dict]) -> str:
    """
    正規化済みアイテムリストの SHA-256 ハッシュを計算する。

    同じ内容なら同じハッシュになるよう、JSON を正規化してハッシュ化。
    """
    # 安定した JSON 文字列化
    serialized = json.dumps(
        items,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


# ============================================================
# 差分計算
# ============================================================

def _item_key(item: dict) -> str:
    """アイテムの同一性を判断するキーを生成する"""
    # URL があれば URL を主キーに、なければ正規化タイトルを使う
    return item.get("url") or item.get("title") or item.get("raw_text") or ""


def compute_diff(old_items: list[dict], new_items: list[dict]) -> dict:
    """
    旧アイテムリストと新アイテムリストを比較し、差分を返す。

    差分の判断基準:
      - _item_key() が一致するものを「同一アイテム」とみなす
      - 同一アイテムで title/date が異なれば「更新」とみなす
      - 新規キーは「追加」、旧キーのみ存在は「削除」

    Returns
    -------
    dict:
        added     : 新規アイテムのリスト
        removed   : 削除アイテムのリスト
        modified  : 更新アイテムのリスト ({"old": {...}, "new": {...}})
        unchanged : 変更なしアイテムのリスト
        has_diff  : 差分があるかどうか
    """
    old_map = {_item_key(item): item for item in old_items if _item_key(item)}
    new_map = {_item_key(item): item for item in new_items if _item_key(item)}

    old_keys = set(old_map.keys())
    new_keys = set(new_map.keys())

    added_keys   = new_keys - old_keys
    removed_keys = old_keys - new_keys
    common_keys  = old_keys & new_keys

    added   = [new_map[k] for k in added_keys]
    removed = [old_map[k] for k in removed_keys]

    modified  = []
    unchanged = []
    for key in common_keys:
        old_item = old_map[key]
        new_item = new_map[key]
        # title または date に変化があれば更新とみなす
        if old_item.get("title") != new_item.get("title") or \
           old_item.get("date")  != new_item.get("date"):
            modified.append({"old": old_item, "new": new_item})
        else:
            unchanged.append(new_item)

    has_diff = bool(added or removed or modified)

    logger.info(
        "差分計算結果: 追加=%d 削除=%d 更新=%d 変化なし=%d",
        len(added), len(removed), len(modified), len(unchanged),
    )

    return {
        "added":     added,
        "removed":   removed,
        "modified":  modified,
        "unchanged": unchanged,
        "has_diff":  has_diff,
    }
