"""
browser.py
Playwright を使ったページアクセス・認証・データ取得を担当。
DOM 操作はすべてここに集約し、parser.py には加工済みデータを渡す。
セレクタ調整: config.py の AUTH_SELECTORS / NOTIFICATION_SELECTORS を編集する。
"""
import asyncio
import logging
from typing import Optional
from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)
from .config import (
    AUTH_SELECTORS,
    ELEMENT_TIMEOUT_MS,
    MAX_RETRIES,
    NETWORK_IDLE_TIMEOUT_MS,
    NOTIFICATION_SELECTORS,
    PAGE_LOAD_TIMEOUT_MS,
    RETRY_WAIT_SECONDS,
    TARGET_URL,
    USER_AGENT,
)

logger = logging.getLogger(__name__)

# ============================================================
# カスタム例外
# ============================================================
class BrowserError(Exception):
    """ブラウザ操作の基底例外"""

class AuthenticationError(BrowserError):
    """認証失敗"""

class ContentExtractionError(BrowserError):
    """通知一覧の取得失敗"""

class PageLoadError(BrowserError):
    """ページ読み込み失敗"""

# ============================================================
# ブラウザ管理
# ============================================================
async def _create_browser_context(playwright) -> tuple[Browser, BrowserContext]:
    """Chromium を headless 起動し、コンテキストを返す"""
    browser = await playwright.chromium.launch(headless=True)
    context = await browser.new_context(
        user_agent=USER_AGENT,
        locale="ja-JP",
        timezone_id="Asia/Tokyo",
        java_script_enabled=True,
    )
    context.set_default_navigation_timeout(PAGE_LOAD_TIMEOUT_MS)
    context.set_default_timeout(ELEMENT_TIMEOUT_MS)
    return browser, context


async def _close_safely(browser: Browser, context: Optional[BrowserContext]) -> None:
    """例外を握りつぶしてブラウザを安全に閉じる"""
    try:
        if context:
            await context.close()
    except Exception:
        pass
    try:
        await browser.close()
    except Exception:
        pass

# ============================================================
# 認証処理
# ============================================================
async def _find_first_selector(page: Page, candidates: list[str]) -> Optional[object]:
    """セレクタ候補リストを順番に試し、最初にマッチした要素を返す"""
    for selector in candidates:
        try:
            el = await page.query_selector(selector)
            if el:
                logger.debug("セレクタマッチ: %s", selector)
                return el
        except Exception:
            continue
    return None


async def _is_auth_required(page: Page) -> bool:
    """ページにパスワード入力フォームがあるか確認する"""
    pw_input = await _find_first_selector(page, AUTH_SELECTORS["password_input"])
    result = pw_input is not None
    logger.debug("認証フォーム検出: %s", result)
    return result


async def _authenticate(page: Page, password: Optional[str]) -> None:
    """
    パスワードフォームを検出して送信する。
    """
    if not password:
        raise AuthenticationError(
            "認証フォームが検出されましたが TARGET_PASSWORD が設定されていません。"
            "GitHub Secrets に TARGET_PASSWORD を登録してください。"
        )

    pw_input = await _find_first_selector(page, AUTH_SELECTORS["password_input"])
    if not pw_input:
        raise AuthenticationError(
            "パスワード入力フィールドが見つかりませんでした。"
            f"候補セレクタ: {AUTH_SELECTORS['password_input']}"
        )

    logger.info("パスワードを入力します (値はログに出力しません)")
    await pw_input.fill(password)

    submit_btn = await _find_first_selector(page, AUTH_SELECTORS["submit_button"])
    if submit_btn:
        logger.info("送信ボタンをクリックします")
        await submit_btn.click()
    else:
        logger.warning(
            "送信ボタンが見つかりませんでした。Enter キーで送信を試みます。"
            f"候補セレクタ: {AUTH_SELECTORS['submit_button']}"
        )
        await pw_input.press("Enter")

    try:
        await page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        logger.debug("networkidle タイムアウト。処理を続行します。")
        await page.wait_for_load_state("domcontentloaded")

    indicator = await _find_first_selector(page, AUTH_SELECTORS["post_auth_indicator"])
    if not indicator:
        still_has_pw = await _is_auth_required(page)
        if still_has_pw:
            raise AuthenticationError(
                "認証後もパスワードフォームが残っています。"
                "パスワードが誤っているか、認証フォームの仕様が異なる可能性があります。"
            )
        logger.warning(
            "認証成功インジケーターが見つかりませんでした。処理を続行します。"
            f"候補セレクタ: {AUTH_SELECTORS['post_auth_indicator']}"
        )
    logger.info("認証成功")

# ============================================================
# 通知アイテム抽出
# ============================================================
async def _extract_single_item(
    el, base_url: str, title_selector: str, date_selector: str
) -> Optional[dict]:
    """
    1つの通知要素からタイトル・URL・日付を抽出する。
    """
    title_text: Optional[str] = None
    href: Optional[str] = None

    for link_selector in NOTIFICATION_SELECTORS["link"]:
        try:
            link_el = await el.query_selector(link_selector)
            if link_el:
                href_raw = await link_el.get_attribute("href")
                if href_raw:
                    from urllib.parse import urljoin
                    href = urljoin(base_url, href_raw.strip())
                title_text = (await link_el.inner_text()).strip()
                break
        except Exception:
            continue

    if not title_text:
        try:
            title_el = await el.query_selector(title_selector)
            if title_el:
                title_text = (await title_el.inner_text()).strip()
        except Exception:
            pass

    if not title_text:
        try:
            title_text = (await el.inner_text()).strip()
        except Exception:
            pass

    if not title_text or not title_text.strip():
        return None

    date_text: Optional[str] = None
    try:
        date_el = await el.query_selector(date_selector)
        if date_el:
            dt_attr = await date_el.get_attribute("datetime")
            date_text = dt_attr.strip() if dt_attr else (await date_el.inner_text()).strip()
    except Exception:
        pass

    return {
        "title": title_text,
        "url": href,
        "date": date_text,
    }


async def _extract_notifications(page: Page, base_url: str) -> list[dict]:
    """
    通知一覧をページから抽出する。

    【修正点】
    以前は query_selector で最初の1コンテナだけ取得していたため、
    複数セクション（特別規則書・公式通知・各種申請など）がある場合に
    先頭の ul のみ（2件）しか取れなかった。
    query_selector_all で全コンテナを取得し、全セクションのアイテムをまとめる。
    """
    # --- 全コンテナを取得 (query_selector_all に変更) ---
    all_items: list[dict] = []
    found_any_container = False

    for selector in NOTIFICATION_SELECTORS["container"]:
        try:
            # ★ query_selector → query_selector_all に変更
            containers = await page.query_selector_all(selector)
            if not containers:
                continue

            logger.info("コンテナ検出: %s (%d個)", selector, len(containers))
            found_any_container = True

            title_selector = NOTIFICATION_SELECTORS["title"][0]
            date_selector  = NOTIFICATION_SELECTORS["date"][0]

            for container in containers:
                # --- 各コンテナからアイテムを取得 ---
                item_elements = []
                used_item_selector = None
                for item_selector in NOTIFICATION_SELECTORS["item"]:
                    try:
                        els = await container.query_selector_all(item_selector)
                        if els:
                            item_elements = els
                            used_item_selector = item_selector
                            break
                    except Exception:
                        continue

                if not item_elements:
                    logger.debug(
                        "コンテナ内にアイテムなし (セレクタ: %s)",
                        NOTIFICATION_SELECTORS["item"],
                    )
                    continue

                logger.debug(
                    "アイテムセレクタ: %s (%d件) [コンテナ: %s]",
                    used_item_selector, len(item_elements), selector,
                )

                for el in item_elements:
                    try:
                        item = await _extract_single_item(
                            el, base_url, title_selector, date_selector
                        )
                        if item:
                            all_items.append(item)
                    except Exception as exc:
                        logger.debug("アイテム抽出中の例外 (スキップ): %s", exc)
                        continue

            # コンテナが見つかったセレクタで全件取得できたらループ終了
            if found_any_container:
                break

        except Exception as exc:
            logger.debug("コンテナ検索中の例外 (セレクタ: %s): %s", selector, exc)
            continue

    if not found_any_container:
        logger.warning(
            "通知コンテナが見つかりません。フォールバック: ページ全体のリンクを収集します。\n"
            "  候補セレクタ: %s\n"
            "  DOM確認方法: ブラウザDevTools → Elements タブで構造を確認し、\n"
            "  config.py の NOTIFICATION_SELECTORS['container'] を更新してください。",
            NOTIFICATION_SELECTORS["container"],
        )
        return await _extract_links_fallback(page, base_url)

    logger.info("抽出アイテム数 (全セクション合計): %d", len(all_items))
    return all_items


async def _extract_links_fallback(page: Page, base_url: str) -> list[dict]:
    """
    フォールバック: ページ内の全リンクを収集する。
    セレクタが全く合わない場合の最終手段。
    """
    from urllib.parse import urljoin
    items = []
    try:
        links = await page.query_selector_all("a[href]")
        for link in links:
            href_raw = await link.get_attribute("href")
            text = (await link.inner_text()).strip()
            if not href_raw or not text:
                continue
            items.append({
                "title": text,
                "url": urljoin(base_url, href_raw.strip()),
                "date": None,
            })
        logger.info("フォールバックで %d 件のリンクを収集", len(items))
    except Exception as exc:
        logger.error("フォールバックリンク収集失敗: %s", exc)
    return items

# ============================================================
# メイン取得関数 (公開 API)
# ============================================================
async def fetch_notifications(
    url: str = TARGET_URL,
    password: Optional[str] = None,
) -> list[dict]:
    """
    対象URLにアクセスし、通知一覧を取得して返す。
    """
    last_exc: Optional[Exception] = None

    for attempt in range(1, MAX_RETRIES + 1):
        browser: Optional[Browser] = None
        context: Optional[BrowserContext] = None
        try:
            async with async_playwright() as playwright:
                browser, context = await _create_browser_context(playwright)
                page = await context.new_page()

                logger.info("[試行 %d/%d] %s に接続中...", attempt, MAX_RETRIES, url)

                try:
                    response = await page.goto(url, wait_until="domcontentloaded")
                    if response and response.status >= 400:
                        raise PageLoadError(f"HTTP {response.status}: {url}")
                except PlaywrightTimeoutError as exc:
                    raise PageLoadError(f"ページ読み込みタイムアウト: {exc}") from exc

                try:
                    await page.wait_for_load_state(
                        "networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS
                    )
                except PlaywrightTimeoutError:
                    logger.debug("networkidle 未達。処理続行。")

                if await _is_auth_required(page):
                    logger.info("認証フォームを検出。パスワードを送信します。")
                    await _authenticate(page, password)
                else:
                    logger.info("認証フォームなし。そのまま通知一覧を取得します。")

                items = await _extract_notifications(page, url)

                if not items:
                    logger.warning(
                        "通知アイテムが 0 件でした。"
                        "セレクタが合っていないか、ページが空の可能性があります。\n"
                        "  PageURL: %s\n"
                        "  PageTitle: %s",
                        page.url,
                        await page.title(),
                    )
                return items

        except (PageLoadError, AuthenticationError, ContentExtractionError):
            raise
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "[試行 %d/%d] 予期しないエラー: %s",
                attempt, MAX_RETRIES, exc,
            )
            if attempt < MAX_RETRIES:
                logger.info("%d秒後にリトライします...", RETRY_WAIT_SECONDS)
                await asyncio.sleep(RETRY_WAIT_SECONDS)
        finally:
            if browser:
                await _close_safely(browser, context)

    raise PageLoadError(
        f"最大リトライ回数 ({MAX_RETRIES}) に達しました。最後のエラー: {last_exc}"
    )
