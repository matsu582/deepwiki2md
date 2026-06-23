#!/usr/bin/env python3
"""
DeepWikiエクスポートツール
ユーザーが手動でログインした後、コンテンツを自動取得してMarkdownに変換する

使用方法:
1. ツールを起動: python deepwiki2md.py <repository_url>
2. ブラウザが開くので、手動でログイン
3. ログイン完了後、Enterキーを押す
4. ツールが自動的にコンテンツを取得してMarkdownに変換

対応プラットフォーム: Windows, Mac, Linux
必要なパッケージ: selenium, beautifulsoup4, webdriver-manager
"""

import os
import sys
import time
import re
import json
import platform
import html
import tempfile
import gettext
import locale
from pathlib import Path

# 多言語化設定（環境変数LANGに基づく）
def setup_i18n():
    """gettextによる多言語化を設定"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    locale_dir = os.path.join(script_dir, 'locale')
    
    # 環境変数LANGから言語を取得
    lang = os.environ.get('LANG', '')
    
    # jaで始まる場合は日本語、それ以外は英語
    if lang.startswith('ja'):
        language = 'ja'
    else:
        language = 'en'
    
    try:
        # 翻訳オブジェクトを取得
        translation = gettext.translation(
            'deepwiki2md',
            localedir=locale_dir,
            languages=[language],
            fallback=True
        )
        return translation.gettext
    except Exception:
        # 翻訳ファイルが見つからない場合はデフォルト（英語）を返す
        return lambda x: x

# グローバル翻訳関数
_ = setup_i18n()

# Seleniumのインポート
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException, NoSuchElementException
except ImportError:
    print(_("Error: selenium is not installed"))
    print(_("Install: pip install selenium"))
    sys.exit(1)

# webdriver-managerのインポート（クロスプラットフォーム対応）
try:
    from webdriver_manager.chrome import ChromeDriverManager
    HAS_WEBDRIVER_MANAGER = True
except ImportError:
    HAS_WEBDRIVER_MANAGER = False
    print(_("Warning: webdriver-manager is not installed"))
    print(_("For automatic ChromeDriver management: pip install webdriver-manager"))

# BeautifulSoupのインポート
try:
    from bs4 import BeautifulSoup
except ImportError:
    print(_("Error: beautifulsoup4 is not installed"))
    print(_("Install: pip install beautifulsoup4"))
    sys.exit(1)

# PNG変換はSeleniumのelement.screenshot()を使用
# cairosvgはforeignObject（HTML埋め込み）を正しくレンダリングできないため不使用

# 既存のMermaid変換モジュールをインポート
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)

try:
    from extract_subgraphs import extract_mermaid_from_svg
    HAS_MERMAID_CONVERTER = True
except ImportError:
    HAS_MERMAID_CONVERTER = False
    print(_("Warning: extract_subgraphs.py not found. Mermaid conversion is disabled."))


# SVGのcamelCase要素マッピング（BeautifulSoupのhtml.parserが小文字化するため）
_SVG_CAMELCASE_TAGS = {
    'foreignobject': 'foreignObject',
    'lineargradient': 'linearGradient',
    'radialgradient': 'radialGradient',
    'clippath': 'clipPath',
    'textpath': 'textPath',
}

_SVG_CAMELCASE_ATTRS = {
    'viewbox': 'viewBox',
    'preserveaspectratio': 'preserveAspectRatio',
}


def fix_svg_camelcase(svg_str):
    """SVGのcamelCase要素と属性を正しいケースに修正"""
    def replace_tag(match):
        slash = match.group(1)
        tag_name = match.group(2).lower()
        if tag_name in _SVG_CAMELCASE_TAGS:
            return f"<{slash}{_SVG_CAMELCASE_TAGS[tag_name]}"
        return match.group(0)
    
    def replace_attr(match):
        attr_name = match.group(1).lower()
        if attr_name in _SVG_CAMELCASE_ATTRS:
            return f" {_SVG_CAMELCASE_ATTRS[attr_name]}="
        return match.group(0)
    
    result = re.sub(r'<(/?)([a-zA-Z]+)', replace_tag, svg_str)
    result = re.sub(r' ([a-zA-Z]+)=', replace_attr, result)
    return result


class DeepWikiExporter:
    """DeepWikiからコンテンツをエクスポートするクラス"""
    
    # サイト種別定数
    SITE_DEVIN = 'devin'  # app.devin.ai/wiki
    SITE_DEEPWIKI = 'deepwiki'  # deepwiki.com
    
    def __init__(self, output_dir='output', diagram_types=None):
        self.output_dir = output_dir
        self.driver = None
        self.wait = None
        self.svg_counter = 0
        self.sections = []
        self.png_driver = None  # PNG変換用のheadlessブラウザ
        self.pending_png_conversions = []  # PNG変換待ちのSVGファイルリスト
        self.site_type = None  # サイト種別（devin または deepwiki）
        
        # 図の出力形式（デフォルト: mermaid,svg）
        if diagram_types is None:
            self.diagram_types = ['mermaid', 'svg']
        else:
            self.diagram_types = [t.strip().lower() for t in diagram_types.split(',')]
        
        # 出力ディレクトリを作成
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(os.path.join(output_dir, 'images'), exist_ok=True)
    
    def detect_site_type(self, url):
        """URLからサイト種別を判定（/org/パス形式にも対応）"""
        if 'deepwiki.com' in url:
            self.site_type = self.SITE_DEEPWIKI
        elif 'app.devin.ai' in url and '/wiki' in url:
            # /org/{org}/wiki/... または /wiki/... の両パターンに対応
            self.site_type = self.SITE_DEVIN
        else:
            self.site_type = self.SITE_DEEPWIKI
        print(_("Site type: {}").format(self.site_type))
    
    def setup_browser(self, headless=False):
        """ブラウザを設定（クロスプラットフォーム対応）"""
        options = Options()
        
        # OS判定
        system = platform.system()
        
        if headless:
            # ヘッドレスモードの設定
            options.add_argument('--headless')
            options.add_argument('--window-size=1920,1080')
            options.add_argument('--disable-gpu')
            
            # Linux専用オプション（コンテナ環境向け）
            if system == 'Linux':
                options.add_argument('--no-sandbox')
                options.add_argument('--disable-dev-shm-usage')
        else:
            # GUIモードの設定
            options.add_argument('--window-size=1920,1080')
        
        # ユーザーデータディレクトリを設定（セッション保持用）
        if system == 'Windows':
            user_data_dir = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'DeepWiki2Md', 'chrome_profile')
        elif system == 'Darwin':  # macOS
            user_data_dir = os.path.expanduser('~/Library/Application Support/DeepWiki2Md/chrome_profile')
        else:  # Linux
            user_data_dir = os.path.expanduser('~/.config/deepwiki2md/chrome_profile')
        
        os.makedirs(user_data_dir, exist_ok=True)
        options.add_argument(f'--user-data-dir={user_data_dir}')
        
        # ChromeDriverの設定
        if HAS_WEBDRIVER_MANAGER:
            try:
                service = Service(ChromeDriverManager().install())
                self.driver = webdriver.Chrome(service=service, options=options)
            except Exception as e:
                print(_("Failed to get ChromeDriver via webdriver-manager: {}").format(e))
                print(_("Using system ChromeDriver..."))
                self.driver = webdriver.Chrome(options=options)
        else:
            self.driver = webdriver.Chrome(options=options)
        
        self.wait = WebDriverWait(self.driver, 30)
        return self.driver
    
    def navigate_and_wait_for_login(self, url, headless=False, email=None):
        """URLに移動し、ユーザーのログインを待つ"""
        print(_("\nOpening browser: {}").format(url))
        self.driver.get(url)
        
        # ページ読み込み待機（deepwiki.comは短縮）
        if self.site_type == self.SITE_DEEPWIKI:
            time.sleep(1)
            print(_("deepwiki.com: No login required"))
            self.wait_for_page_load(timeout=3)
            return True
        
        time.sleep(3)
        current_url = self.driver.current_url
        
        # app.devin.ai/wikiの場合はログインが必要かどうかを確認
        if 'auth' in current_url or 'login' in current_url or 'sign' in current_url:
            if headless:
                # ヘッドレスモード: CUIでログイン情報を入力
                if self._is_login_page() or self._is_code_page():
                    login_success = self._handle_cui_login(email=email)
                    if not login_success:
                        print(_("Login failed. Exiting."))
                        return False
                    # ログイン後、元のURLに戻る
                    self.driver.get(url)
            else:
                # GUIモード: ユーザーに手動ログインを促す
                print("\n" + "="*60)
                print(_("Login required."))
                
                # -eオプションでメールアドレスが指定されている場合は自動入力
                if email and self._is_login_page():
                    print(_("Auto-filling email address: {}").format(email))
                    try:
                        # 新構造: input[type="email"]、旧構造: input#username
                        try:
                            email_input = self.driver.find_element(
                                By.CSS_SELECTOR, 'input[type="email"]'
                            )
                        except NoSuchElementException:
                            email_input = self.driver.find_element(
                                By.ID, 'username'
                            )
                        email_input.clear()
                        email_input.send_keys(email)
                        
                        # 送信ボタンをクリック
                        submit_btn = self.driver.find_element(
                            By.CSS_SELECTOR,
                            'button[type="submit"]'
                        )
                        submit_btn.click()
                        print(_("Email entered and Continue button clicked."))
                        print(_("Authentication code will be sent to your email."))
                        time.sleep(3)
                    except Exception as e:
                        print(_("Failed to auto-fill email: {}").format(e))
                
                print(_("Please complete login in the browser."))
                print(_("Press Enter in this terminal after login is complete."))
                print("="*60 + "\n")
                input(_(">>> Press Enter after login is complete: "))
                
                # ログイン後、元のURLに戻る
                self.driver.get(url)
        
        self.wait_for_page_load()
        return True
    
    def wait_for_page_load(self, timeout=5):
        """SPAのコンテンツが読み込まれるまで待機（高速版）"""
        try:
            # サイト種別に応じてセレクタを選択
            if self.site_type == self.SITE_DEEPWIKI:
                # deepwiki.com用: proseベースのコンテンツ領域を検出
                selectors = ['div[class*="prose-custom"]', 'div[class*="prose"]', 'h1', 'article', 'main']
                extra_wait = 0.2
            else:
                # app.devin.ai/wiki用: prose-mainベースのコンテンツ領域を検出
                selectors = ['div.prose-main', 'div[class*="prose"]', 'h1', 'article', 'main']
                extra_wait = 0.5
            
            # 短いタイムアウトで各セレクタを試行
            local_wait = WebDriverWait(self.driver, timeout)
            
            for selector in selectors:
                try:
                    local_wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
                    break
                except TimeoutException:
                    continue
            
            # 最小限の追加待機（DOMの安定化用）
            time.sleep(extra_wait)
            
        except Exception as e:
            print(_("  Warning: Error while waiting for page load: {}").format(e))
    
    def _is_login_page(self):
        """ログインページかどうかを判定（メールアドレス入力ページ）"""
        try:
            # 新構造: input[type="email"] （IDなし）
            self.driver.find_element(
                By.CSS_SELECTOR, 'input[type="email"]'
            )
            return True
        except NoSuchElementException:
            pass
        # 旧構造: input#username
        try:
            self.driver.find_element(By.ID, 'username')
            return True
        except NoSuchElementException:
            return False
    
    def _is_code_page(self):
        """認証コード入力ページかどうかを判定"""
        try:
            # 新構造: input[autocomplete="one-time-code"] （IDなし）
            self.driver.find_element(
                By.CSS_SELECTOR,
                'input[autocomplete="one-time-code"]'
            )
            return True
        except NoSuchElementException:
            pass
        # 旧構造: input#code
        try:
            self.driver.find_element(By.ID, 'code')
            return True
        except NoSuchElementException:
            return False
    
    def _handle_cui_login(self, email=None):
        """CUIベースのログイン処理（メール＋認証コード方式）"""
        max_attempts = 3
        
        for attempt in range(max_attempts):
            if self._is_login_page():
                print("\n" + "="*60)
                print(_("Login page detected (headless mode)"))
                print("="*60)
                
                # emailが指定されていない場合は入力プロンプトを表示
                if email:
                    email_to_use = email
                    print(_("Email address: {}").format(email_to_use))
                else:
                    email_to_use = input(_("Enter email address: ")).strip()
                
                if not email_to_use:
                    print(_("Email address not entered"))
                    continue
                
                try:
                    # 新構造: input[type="email"] を優先、旧構造: input#username
                    try:
                        email_input = self.driver.find_element(
                            By.CSS_SELECTOR, 'input[type="email"]'
                        )
                    except NoSuchElementException:
                        email_input = self.driver.find_element(
                            By.ID, 'username'
                        )
                    email_input.clear()
                    email_input.send_keys(email_to_use)
                    
                    # 新構造: button[type="submit"]、旧構造: button[name="action"]
                    try:
                        submit_btn = self.driver.find_element(
                            By.CSS_SELECTOR,
                            'button[type="submit"]'
                        )
                    except NoSuchElementException:
                        submit_btn = self.driver.find_element(
                            By.CSS_SELECTOR,
                            'button[type="submit"][name="action"]'
                        )
                    submit_btn.click()
                    
                    time.sleep(3)
                except Exception as e:
                    print(_("Email input error: {}").format(e))
                    continue
            
            if self._is_code_page():
                print("\n" + "="*60)
                print(_("Authentication code input page detected"))
                print(_("Please enter the authentication code sent to your email"))
                print("="*60)
                
                code = input(_("Enter authentication code: ")).strip()
                if not code:
                    print(_("Authentication code not entered"))
                    continue
                
                try:
                    # 新構造: input[autocomplete="one-time-code"]、旧構造: input#code
                    try:
                        code_input = self.driver.find_element(
                            By.CSS_SELECTOR,
                            'input[autocomplete="one-time-code"]'
                        )
                    except NoSuchElementException:
                        code_input = self.driver.find_element(
                            By.ID, 'code'
                        )
                    code_input.clear()
                    code_input.send_keys(code)
                    
                    # 新構造: button[type="submit"]（テキスト"Continue"）
                    submit_btn = self.driver.find_element(
                        By.CSS_SELECTOR,
                        'button[type="submit"]'
                    )
                    submit_btn.click()
                    
                    time.sleep(3)
                except Exception as e:
                    print(_("Authentication code input error: {}").format(e))
                    continue
            
            if not self._is_login_page() and not self._is_code_page():
                print(_("Login successful"))
                return True
        
        print(_("Login failed"))
        return False
    
    def select_language(self, lang):
        """「...」メニュー内の言語サブメニューから言語を選択"""
        print(_("\nSelecting language: {}").format(lang))
        
        lang_map = {
            'japanese': 'Japanese',
            'english': 'English',
            'chinese': 'Chinese',
            'korean': 'Korean',
            'spanish': 'Spanish',
            'french': 'French',
            'german': 'German',
            'portuguese': 'Portuguese',
            'russian': 'Russian',
            'italian': 'Italian'
        }
        
        target_lang = lang_map.get(lang.lower(), lang.capitalize())
        
        try:
            # 「More actions」ボタン（...メニュー）を探す
            more_btn = self._find_more_actions_button()
            if not more_btn:
                print(_("  Warning: More actions button not found"))
                return False
            
            # メニューを開く
            self.driver.execute_script(
                "arguments[0].click();", more_btn
            )
            time.sleep(0.5)
            
            # Languageメニュー項目を探してホバー
            lang_item = self._find_language_menu_item()
            if not lang_item:
                print(_("  Warning: Language menu item not found"))
                self._close_menu(more_btn)
                return False
            
            # 現在選択中の言語を確認
            item_text = lang_item.text.strip()
            if target_lang.lower() in item_text.lower():
                # 既に対象言語かもしれない（テキストに含まれるか確認）
                pass
            
            # Languageサブメニューを開く（ホバーまたはクリック）
            from selenium.webdriver.common.action_chains import ActionChains
            actions = ActionChains(self.driver)
            actions.move_to_element(lang_item).perform()
            time.sleep(0.5)
            
            # サブメニュー内のmenuitemradioから言語を選択
            selected = self._select_language_radio(target_lang)
            if selected:
                print(_("  Language selected: {}").format(target_lang))
                time.sleep(1.5)
                self.wait_for_page_load()
                return True
            
            print(_("  Warning: Language '{}' not found in submenu").format(
                target_lang
            ))
            self._close_menu(more_btn)
            return False
                
        except Exception as e:
            print(_("  Language selection error: {}").format(e))
            return False
    
    def _find_more_actions_button(self):
        """「...」メニューボタンを検索"""
        selectors = [
            'button[aria-label="More actions"]',
            'button[aria-label="その他のアクション"]',
        ]
        for sel in selectors:
            try:
                elems = self.driver.find_elements(By.CSS_SELECTOR, sel)
                if elems:
                    return elems[0]
            except Exception:
                continue
        # フォールバック: aria-expandedを持つ最後のヘッダーボタン
        try:
            header_btns = self.driver.find_elements(
                By.CSS_SELECTOR, 'header button[aria-expanded]'
            )
            if header_btns:
                return header_btns[-1]
        except Exception:
            pass
        return None
    
    def _find_language_menu_item(self):
        """メニュー内のLanguage項目を検索"""
        try:
            items = self.driver.find_elements(
                By.CSS_SELECTOR, '[role="menuitem"]'
            )
            for item in items:
                txt = item.text.strip().lower()
                if 'language' in txt or '言語' in txt:
                    return item
        except Exception:
            pass
        return None
    
    def _select_language_radio(self, target_lang):
        """menuitemradioから対象言語を選択"""
        try:
            radios = self.driver.find_elements(
                By.CSS_SELECTOR, '[role="menuitemradio"]'
            )
            for radio in radios:
                radio_text = radio.text.strip()
                if radio_text.lower() == target_lang.lower():
                    self.driver.execute_script(
                        "arguments[0].click();", radio
                    )
                    return True
        except Exception:
            pass
        # XPathフォールバック
        try:
            radio_el = self.driver.find_element(
                By.XPATH,
                "//*[@role='menuitemradio' and "
                "contains(text(), '{}')]".format(target_lang)
            )
            self.driver.execute_script(
                "arguments[0].click();", radio_el
            )
            return True
        except Exception:
            pass
        return False
    
    def _close_menu(self, trigger_btn):
        """メニューを閉じる"""
        try:
            self.driver.execute_script(
                "arguments[0].click();", trigger_btn
            )
        except Exception:
            pass
    
    def get_wiki_sections(self):
        """DeepWikiのセクション一覧を取得（サイト種別に応じて処理）"""
        sections = []
        
        try:
            if self.site_type == self.SITE_DEEPWIKI:
                # deepwiki.com: <a>タグベースのナビゲーション
                sections = self._get_sections_deepwiki()
            else:
                # app.devin.ai/wiki: <a>タグベースのナビゲーション（/page/X.Y形式）
                sections = self._get_sections_devin()
            
        except Exception as e:
            print(_("  Section retrieval error: {}").format(e))
            import traceback
            traceback.print_exc()
        
        return sections
    
    def _get_sections_deepwiki(self):
        """deepwiki.com用のセクション取得（<a>タグベース）"""
        sections = []
        
        # サイドバーの<a>要素を取得（新旧両方の構造に対応）
        sidebar_selectors = [
            'ul li a[data-selected]',
            'ul[class*="overflow-y-auto"][class*="space-y"] li a',
            'ul li a[href*="/"]',
            '[class*="sidebar"] li a',
            'aside li a',
            'nav ul li a'
        ]
        
        # サイドバーが完全に読み込まれるまで待機
        links = []
        best_selector = None
        max_wait = 10
        for wait_count in range(max_wait):
            for selector in sidebar_selectors:
                try:
                    found = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    # deepwiki.comのリンクのみをフィルタ
                    valid_links = [
                        link for link in found 
                        if link.get_attribute('href') and 
                        ('deepwiki.com/' in link.get_attribute('href') or 
                         link.get_attribute('href').startswith('/'))
                    ]
                    if len(valid_links) > len(links):
                        links = valid_links
                        best_selector = selector
                except NoSuchElementException:
                    continue
            
            if len(links) >= 3:
                break
            
            time.sleep(1)
        
        if links and best_selector:
            print(_("  Sidebar detected: {} ({} items)").format(best_selector, len(links)))
        
        if not links:
            print(_("  Warning: Sidebar link elements not found"))
            return sections
        
        # 各リンクの情報を収集
        for idx, link in enumerate(links):
            try:
                text = link.text.strip()
                if not text:
                    continue
                
                href = link.get_attribute('href') or ''
                
                # 階層レベルを判定（URLのパス構造から推測）
                # 例: /owner/repo/1-overview -> level 0
                # 例: /owner/repo/2.1-subsection -> level 1
                level = 0
                path_parts = href.split('/')
                if path_parts:
                    last_part = path_parts[-1]
                    # 2.1-xxx, 3.2.1-xxx のようなパターンを検出
                    dot_count = last_part.split('-')[0].count('.') if '-' in last_part else 0
                    level = dot_count
                
                sections.append({
                    'title': text,
                    'index': idx,
                    'level': level,
                    'href': href
                })
                
            except Exception as e:
                print(_("  Link info retrieval error (index={}): {}").format(idx, e))
                continue
        
        print(_("  Retrieved sections: {}").format(len(sections)))
        for s in sections[:5]:
            indent = "  " * s['level']
            print("    {}[L{}] {}".format(indent, s['level'], s['title']))
        if len(sections) > 5:
            print(_("    ... and {} more").format(len(sections) - 5))
        
        return sections
    
    def _get_sections_devin(self):
        """app.devin.ai/wiki用のセクション取得（<a>タグベース、/page/X.Y形式）"""
        sections = []
        
        # サイドバーのリンク要素を取得（ボタンからリンクに変更された構造に対応）
        sidebar_selectors = [
            'a[href*="/page/"]',
            'ul li a[aria-label]',
            'ul li div a[href*="/wiki/"]',
        ]
        
        # サイドバーが完全に読み込まれるまで待機
        links = []
        best_selector = None
        max_wait = 10
        for wait_count in range(max_wait):
            for selector in sidebar_selectors:
                try:
                    found = self.driver.find_elements(
                        By.CSS_SELECTOR, selector
                    )
                    # wikiページリンクのみをフィルタ
                    valid = [
                        lnk for lnk in found
                        if lnk.get_attribute('href')
                        and '/page/' in lnk.get_attribute('href')
                    ]
                    if len(valid) > len(links):
                        links = valid
                        best_selector = selector
                except NoSuchElementException:
                    continue
            
            if len(links) >= 3:
                break
            
            time.sleep(1)
        
        if links and best_selector:
            print(_("  Sidebar detected: {} ({} items)").format(
                best_selector, len(links)
            ))
        
        if not links:
            print(_("  Warning: Sidebar link elements not found"))
            return sections
        
        # 各リンクの情報を収集（hrefの/page/X.Y形式から階層レベルを判定）
        seen_hrefs = set()
        for idx, link in enumerate(links):
            try:
                # aria-labelまたはテキストからタイトルを取得
                text = link.get_attribute('aria-label') or ''
                if not text:
                    text = link.text.strip()
                    # 親要素のテキストを試す
                    if not text:
                        parent = link.find_element(By.XPATH, '..')
                        text = parent.text.strip()
                if not text:
                    continue
                
                href = link.get_attribute('href') or ''
                
                # 同一hrefのリンクが複数存在する場合、最初のもののみ採用
                href_path = re.sub(r'^https?://[^/]+', '', href)
                if href_path in seen_hrefs:
                    continue
                seen_hrefs.add(href_path)
                
                # /page/X.Y のパターンからレベルを判定
                level = 0
                page_match = re.search(r'/page/([\d.]+)$', href)
                if page_match:
                    page_num = page_match.group(1)
                    level = page_num.count('.')
                
                sections.append({
                    'title': text,
                    'index': idx,
                    'level': level,
                    'href': href
                })
                
            except Exception as e:
                print(_("  Link info retrieval error (index={}): {}").format(
                    idx, e
                ))
                continue
        
        print(_("  Retrieved sections: {}").format(len(sections)))
        for s in sections[:5]:
            indent = "  " * s['level']
            print("    {}[L{}] {}".format(indent, s['level'], s['title']))
        if len(sections) > 5:
            print(_("    ... and {} more").format(len(sections) - 5))
        
        return sections
    
    def scroll_to_load_all_content(self):
        """ページ全体をスクロールして遅延読み込みコンテンツを取得"""
        print(_("  Scrolling page to load content..."))
        
        try:
            # ページの高さを取得
            last_height = self.driver.execute_script("return document.body.scrollHeight")
            
            while True:
                # 下にスクロール
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1)
                
                # 新しい高さを取得
                new_height = self.driver.execute_script("return document.body.scrollHeight")
                
                if new_height == last_height:
                    break
                last_height = new_height
            
            # 最上部に戻る
            self.driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(1)
            
        except Exception as e:
            print(_("  Scroll error: {}").format(e))
    
    def extract_page_html(self):
        """現在のページのHTMLを取得"""
        try:
            # サイト種別に応じてセレクタを選択
            if self.site_type == self.SITE_DEEPWIKI:
                # deepwiki.com用: prose-customがメインコンテンツ領域
                content_selectors = [
                    'div[class*="prose-custom"]',
                    'div[class*="prose"]',
                    'article', 'main',
                ]
            else:
                # app.devin.ai/wiki用: prose-mainがメインコンテンツ領域
                content_selectors = [
                    'div.prose-main',
                    'div[class*="prose"]',
                    'article', 'main',
                ]
            
            for selector in content_selectors:
                try:
                    elems = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    # 最もコンテンツ量の多い要素を選択（小さなprose要素を除外）
                    best_elem = None
                    best_len = 0
                    for elem in elems:
                        html = elem.get_attribute('innerHTML') or ''
                        if len(html) > best_len:
                            best_len = len(html)
                            best_elem = elem
                    if best_elem and best_len > 100:
                        return best_elem.get_attribute('innerHTML')
                except NoSuchElementException:
                    continue
            
            # フォールバック: body全体
            html = self.driver.find_element(By.TAG_NAME, 'body').get_attribute('innerHTML')
            return html
            
        except Exception as e:
            print(_("  HTML retrieval error: {}").format(e))
            return ""
    
    def convert_html_to_markdown(self, html_content, page_name):
        """HTMLをMarkdownに変換"""
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # ナビゲーション要素を除去
        for nav in soup.find_all(['nav', 'header', 'footer']):
            nav.decompose()
        
        # 特定のクラスを持つ要素を除去（UI要素など）
        # deepwiki.comでは本文が影響を受ける可能性があるため、より慎重に処理
        if self.site_type != self.SITE_DEEPWIKI:
            for elem in soup.find_all(class_=re.compile(r'sidebar|navigation|menu|breadcrumb')):
                elem.decompose()
        
        
        md_parts = []
        self.svg_counter = 0
        
        for elem in soup.children:
            result = self._convert_element(elem, page_name)
            if result:
                md_parts.append(result)
        
        md_content = ''.join(md_parts)
        
        # 後処理
        md_content = md_content.replace('Link copied!', '')
        md_content = re.sub(r'\n{3,}', '\n\n', md_content)
        
        # deepwiki.com専用: 不要な行を除去（行単位で安全に処理）
        if self.site_type == self.SITE_DEEPWIKI:
            # Menu# を Menu\n# に分割（改行なしで連結されている場合の対策）
            md_content = md_content.replace('Menu# ', 'Menu\n# ')
            
            lines = md_content.split('\n')
            cleaned = []
            for i, line in enumerate(lines):
                # 先頭50行以内の不要な行をスキップ
                if i < 50:
                    if 'Index your code' in line:
                        continue
                    if 'Last indexed' in line:
                        continue
                    if line.strip() == 'Menu':
                        continue
                cleaned.append(line)
            md_content = '\n'.join(cleaned)
            
            # ページ先頭の目次リスト（サイドバーからの重複）を削除
            # 最初のh1より前にあるリストブロック（- で始まる行が連続）を削除
            lines = md_content.split('\n')
            
            if lines:
                # 最初のh1を探す
                first_h1_idx = -1
                for i, line in enumerate(lines):
                    if line.strip().startswith('# ') and not line.strip().startswith('## '):
                        first_h1_idx = i
                        break
                
                if first_h1_idx > 0:
                    # h1より前の範囲を調べる
                    pre_lines = lines[:first_h1_idx]
                    
                    # `- `で始まる行が連続しているブロックを探す
                    nav_start = None
                    nav_end = None
                    for i, line in enumerate(pre_lines):
                        if line.strip().startswith('- '):
                            if nav_start is None:
                                nav_start = i
                            nav_end = i
                        else:
                            # ブロックが始まった後で`- `でない行が来たら終了
                            if nav_start is not None and nav_end is not None:
                                # 空行は許容する
                                if line.strip() == '':
                                    continue
                                break
                    
                    if nav_start is not None and nav_end is not None:
                        nav_len = nav_end - nav_start + 1
                        # 5行以上の`- `が連続していればナビとみなす
                        if nav_len >= 5:
                            # そのブロックだけ削除
                            lines = lines[:nav_start] + lines[nav_end + 1:]
                            md_content = '\n'.join(lines)
        
        # app.devin.ai専用: ページ先頭の目次リスト（サイドバーからの重複）を削除
        # deepwiki.comでは本文を消す可能性があるため、このロジックは適用しない
        if self.site_type == self.SITE_DEVIN:
            lines = md_content.split('\n')
            if lines:
                first_h1_idx = -1
                first_h1_text = ''
                for i, line in enumerate(lines):
                    if line.startswith('# ') and not line.startswith('## '):
                        first_h1_idx = i
                        first_h1_text = line
                        break
                
                if first_h1_idx >= 0 and first_h1_text:
                    # 同じh1が後にあるか探す（重複目次の削除）
                    second_h1_idx = -1
                    for i in range(first_h1_idx + 1, len(lines)):
                        if lines[i] == first_h1_text:
                            second_h1_idx = i
                            break
                    
                    if second_h1_idx > first_h1_idx:
                        between_lines = lines[first_h1_idx + 1:second_h1_idx]
                        list_lines = [l for l in between_lines if l.strip().startswith('- ')]
                        if len(list_lines) > 5:
                            lines = lines[:first_h1_idx] + lines[second_h1_idx:]
                            md_content = '\n'.join(lines)
        
        return md_content.strip()
    
    def _convert_element(self, elem, page_name, depth=0):
        """HTML要素をMarkdownに変換"""
        if elem.name is None:
            text = elem.string or ''
            return text.strip()
        
        tag = elem.name.lower()
        
        # スキップすべき要素
        skip_tags = ['style', 'script', 'nav', 'header', 'footer', 'button', 
                     'input', 'textarea', 'form', 'iframe', 'noscript']
        if tag in skip_tags:
            return ''
        
        # 見出し
        if tag == 'h1':
            return f"# {elem.get_text(strip=True)}\n\n"
        elif tag == 'h2':
            return f"## {elem.get_text(strip=True)}\n\n"
        elif tag == 'h3':
            return f"### {elem.get_text(strip=True)}\n\n"
        elif tag == 'h4':
            return f"#### {elem.get_text(strip=True)}\n\n"
        
        # 段落
        elif tag == 'p':
            text = self._process_inline(elem)
            if text.strip():
                return f"{text}\n\n"
            return ''
        
        # コードブロック
        elif tag == 'pre':
            svg = elem.find('svg')
            if svg:
                return self._convert_svg(svg, page_name)
            
            code = elem.find('code')
            if code:
                lang = code.get('data-lang', '') or code.get('class', '')
                if 'language-' in str(lang):
                    lang_match = re.search(r'language-(\w+)', str(lang))
                    lang = lang_match.group(1) if lang_match else ''
                
                # <br>タグを改行に変換（行区切りとして使われている場合）
                for br in code.find_all('br'):
                    br.replace_with('\n')
                
                # spanなどのインライン要素はそのまま連結（separatorは空）
                text = code.get_text(separator='', strip=False)
                # 改行コードをLFに正規化
                text = text.replace("\r\n", "\n").replace("\r", "\n")
                
                if text.strip():
                    return f"```{lang}\n{text}\n```\n\n"
            return ''
        
        # インラインコード
        elif tag == 'code':
            return f"`{elem.get_text()}`"
        
        # 強調
        elif tag in ['strong', 'b']:
            return f"**{self._process_inline(elem)}**"
        elif tag in ['em', 'i']:
            return f"*{self._process_inline(elem)}*"
        
        # リンク
        elif tag == 'a':
            text = self._process_inline(elem)
            href = elem.get('href', '')
            if href and text:
                return f"[{text}]({href})"
            return text
        
        # リスト
        elif tag == 'ul':
            items = []
            for li in elem.find_all('li', recursive=False):
                item_text = self._process_inline(li).strip()
                items.append(f"- {item_text}")
            return '\n'.join(items) + '\n\n'
        
        elif tag == 'ol':
            items = []
            for i, li in enumerate(elem.find_all('li', recursive=False), 1):
                item_text = self._process_inline(li).strip()
                items.append(f"{i}. {item_text}")
            return '\n'.join(items) + '\n\n'
        
        # テーブル
        elif tag == 'table':
            return self._convert_table(elem)
        
        # SVG
        elif tag == 'svg':
            return self._convert_svg(elem, page_name)
        
        # details/summary（折りたたみ要素）
        elif tag == 'details':
            summary_elem = elem.find('summary')
            summary_text = summary_elem.get_text(strip=True) if summary_elem else 'Details'
            
            body_parts = []
            for child in elem.children:
                if child.name == 'summary':
                    continue
                child_result = self._convert_element(child, page_name, depth + 1)
                if child_result:
                    body_parts.append(child_result)
            
            body_md = ''.join(body_parts).strip()
            return f"<details>\n<summary>{summary_text}</summary>\n\n{body_md}\n\n</details>\n\n"
        
        elif tag == 'summary':
            return ''
        
        # コンテナ要素
        elif tag in ['div', 'span', 'section', 'article', 'main']:
            result = []
            for child in elem.children:
                child_result = self._convert_element(child, page_name, depth + 1)
                if child_result:
                    result.append(child_result)
            return ''.join(result)
        
        # その他
        else:
            text = self._process_inline(elem)
            if text.strip():
                return text
            return ''
    
    def _process_inline(self, elem):
        """インライン要素を処理"""
        if elem.string:
            return elem.string
        
        parts = []
        for child in elem.children:
            if child.name is None:
                parts.append(child.string or '')
            elif child.name == 'code':
                parts.append(f"`{child.get_text()}`")
            elif child.name in ['strong', 'b']:
                parts.append(f"**{self._process_inline(child)}**")
            elif child.name in ['em', 'i']:
                parts.append(f"*{self._process_inline(child)}*")
            elif child.name == 'a':
                text = self._process_inline(child)
                href = child.get('href', '')
                if href:
                    parts.append(f"[{text}]({href})")
                else:
                    parts.append(text)
            elif child.name == 'br':
                parts.append('\n')
            else:
                parts.append(self._process_inline(child))
        
        return ''.join(parts)
    
    def _convert_table(self, table_elem):
        """テーブルをMarkdownに変換"""
        rows = []
        
        # ヘッダー行
        thead = table_elem.find('thead')
        if thead:
            header_row = thead.find('tr')
            if header_row:
                cells = [self._process_inline(th).strip() for th in header_row.find_all(['th', 'td'])]
                if cells:
                    rows.append('| ' + ' | '.join(cells) + ' |')
                    rows.append('| ' + ' | '.join(['---'] * len(cells)) + ' |')
        
        # ボディ行
        tbody = table_elem.find('tbody') or table_elem
        for tr in tbody.find_all('tr'):
            if tr.parent.name == 'thead':
                continue
            cells = [self._process_inline(td).strip() for td in tr.find_all(['td', 'th'])]
            if cells:
                rows.append('| ' + ' | '.join(cells) + ' |')
        
        if rows:
            return '\n'.join(rows) + '\n\n'
        return ''
    
    def _convert_svg(self, svg_elem, page_name):
        """SVGをファイルに保存し、指定された形式で出力"""
        svg_str = str(svg_elem)
        
        # 小さいアイコンSVGはスキップ
        svg_width = svg_elem.get('width', '')
        svg_height = svg_elem.get('height', '')
        try:
            w_val = float(re.sub(r'[^\d.]', '', svg_width)) if svg_width else 0
            h_val = float(re.sub(r'[^\d.]', '', svg_height)) if svg_height else 0
            if w_val > 0 and h_val > 0 and w_val < 100 and h_val < 100:
                return ''
        except:
            pass
        
        # Mermaid図かどうかをチェック
        svg_id = svg_elem.get('id', '')
        svg_classes = svg_elem.get('class', [])
        if isinstance(svg_classes, str):
            svg_classes = svg_classes.split()
        
        is_mermaid = svg_id.startswith('mermaid-') or any('mermaid' in cls.lower() for cls in svg_classes)
        has_diagram_content = bool(svg_elem.find(class_=re.compile(r'node|edge|actor|cluster|statediagram')))
        
        if not is_mermaid and not has_diagram_content:
            viewbox = svg_elem.get('viewBox', '')
            if viewbox:
                parts = viewbox.split()
                if len(parts) == 4:
                    try:
                        vw, vh = float(parts[2]), float(parts[3])
                        if vw < 100 and vh < 100:
                            return ''
                    except:
                        pass
        
        # 空のSVGはスキップ
        svg_str_clean = re.sub(r'<style[^>]*>.*?</style>', '', svg_str, flags=re.DOTALL)
        if not re.search(r'<(path|rect|polygon|circle|line|text)\b', svg_str_clean):
            return ''
        
        self.svg_counter += 1
        base_filename = f"{page_name}_{self.svg_counter:02d}"
        svg_str_export = fix_svg_camelcase(svg_str)
        
        # 各形式のコンテンツを生成
        outputs = {}
        
        # SVG出力
        if 'svg' in self.diagram_types:
            svg_filename = f"{base_filename}.svg"
            svg_path = os.path.join(self.output_dir, 'images', svg_filename)
            try:
                with open(svg_path, 'w', encoding='utf-8') as f:
                    f.write(svg_str_export)
                outputs['svg'] = f"![図](images/{svg_filename})"
            except Exception as e:
                print(_("  SVG save error: {}").format(e))
        
        # PNG出力（SVGを保存してから後でブラウザで変換）
        if 'png' in self.diagram_types:
            png_filename = f"{base_filename}.png"
            png_path = os.path.join(self.output_dir, 'images', png_filename)
            # PNG変換にはSVGファイルが必要なので、SVGも保存
            svg_for_png_path = os.path.join(self.output_dir, 'images', f"{base_filename}.svg")
            if not os.path.exists(svg_for_png_path):
                try:
                    with open(svg_for_png_path, 'w', encoding='utf-8') as f:
                        f.write(svg_str_export)
                except Exception as e:
                    print(_("  SVG save error (for PNG): {}").format(e))
            # PNG変換をキューに追加（後で一括処理）
            self.pending_png_conversions.append((svg_for_png_path, png_path))
            outputs['png'] = f"![図](images/{png_filename})"
        
        # Mermaid出力
        if 'mermaid' in self.diagram_types:
            if HAS_MERMAID_CONVERTER:
                try:
                    mermaid_code = extract_mermaid_from_svg(svg_str_clean)
                    if mermaid_code and len(mermaid_code) > 20:
                        outputs['mermaid'] = f"```mermaid\n{mermaid_code}\n```"
                except Exception as e:
                    print(_("  Mermaid conversion error: {}").format(e))
        
        if not outputs:
            return ''
        
        # 出力形式に応じてMarkdownを生成
        result_parts = []
        is_first = True
        
        for dtype in self.diagram_types:
            if dtype not in outputs:
                continue
            
            content = outputs[dtype]
            
            if is_first:
                # 最初の形式は直接表示
                result_parts.append(content + "\n\n")
                is_first = False
            else:
                # 2番目以降はdetailsで折りたたみ
                label = {'svg': _('SVG Diagram'), 'png': _('PNG Image'), 'mermaid': _('Mermaid Notation')}
                summary = label.get(dtype, dtype)
                result_parts.append(f"<details>\n<summary>{_('Show {format}').format(format=summary)}</summary>\n\n{content}\n\n</details>\n\n")
        
        return ''.join(result_parts)
    
    def export_section_by_click(self, section, chapter_number, total):
        """1セクションをクリックしてエクスポート"""
        title = section['title']
        elem_index = section['index']
        level = section['level']
        
        print(_("\n[{}] Exporting: {} (level {})").format(chapter_number, title, level))
        
        try:
            if self.site_type == self.SITE_DEEPWIKI:
                # deepwiki.com: リンクをクリックしてナビゲーション
                self._navigate_deepwiki(section)
            else:
                # app.devin.ai/wiki: リンクをクリックまたはURL直接遷移
                self._navigate_devin(section)
            
            # ページ読み込み待機
            self.wait_for_page_load()
            
        except Exception as e:
            print(_("  Navigation error: {}").format(e))
            return None
        
        # HTMLを取得
        html_content = self.extract_page_html()
        
        # ファイル名を生成（章番号形式）
        safe_title = self.sanitize_filename(title)
        page_name = f"{chapter_number}_{safe_title}"
        
        # Markdownに変換
        self.svg_counter = 0  # SVGカウンタをリセット
        md_content = self.convert_html_to_markdown(html_content, page_name)
        
        # タイトルを追加（なければ）
        if not md_content.startswith('#'):
            md_content = f"# {title}\n\n{md_content}"
        
        # 保存
        md_path = os.path.join(self.output_dir, f"{page_name}.md")
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        print(_("  Saved: {}").format(md_path))
        
        return {
            'chapter_number': chapter_number,
            'title': title,
            'level': level,
            'filename': f"{page_name}.md",
            'page_name': page_name,
            'href': section.get('href', '')
        }
    
    def _navigate_deepwiki(self, section):
        """deepwiki.com用のナビゲーション（リンククリック優先、SPAルーティング活用）"""
        elem_index = section['index']
        href = section.get('href', '')
        
        # サイドバーのリンクをクリック（SPAルーティングを活用して高速化）
        sidebar_selectors = [
            'ul li a[data-selected]',
            'ul li a[href*="/"]',
            '[class*="sidebar"] li a',
            'aside li a',
            'nav ul li a'
        ]
        
        links = []
        for selector in sidebar_selectors:
            try:
                found = self.driver.find_elements(By.CSS_SELECTOR, selector)
                valid_links = [
                    link for link in found 
                    if link.get_attribute('href') and 
                    ('deepwiki.com/' in link.get_attribute('href') or 
                     link.get_attribute('href').startswith('/'))
                ]
                if valid_links:
                    links = valid_links
                    break
            except:
                continue
        
        # リンククリックを試行（SPAルーティング）
        if elem_index < len(links):
            link = links[elem_index]
            try:
                WebDriverWait(self.driver, 2).until(EC.element_to_be_clickable(link))
                link.click()
                return
            except Exception as e:
                print(_("  Link click failed, falling back to URL navigation: {}").format(e))
        
        # フォールバック: 直接URLに移動
        if href:
            if not href.startswith('http'):
                href = f"https://deepwiki.com{href}"
            self.driver.get(href)
        else:
            raise Exception(_("Link index {} is out of range and href is not available").format(elem_index))
    
    def _navigate_devin(self, section):
        """app.devin.ai/wiki用のナビゲーション（リンククリックまたはURL直接遷移）"""
        elem_index = section['index']
        href = section.get('href', '')
        
        # サイドバーのリンクを取得してクリックを試行
        sidebar_selectors = [
            'a[href*="/page/"]',
            'ul li a[aria-label]',
        ]
        
        links = []
        for selector in sidebar_selectors:
            try:
                found = self.driver.find_elements(
                    By.CSS_SELECTOR, selector
                )
                valid = [
                    lnk for lnk in found
                    if lnk.get_attribute('href')
                    and '/page/' in lnk.get_attribute('href')
                ]
                if valid:
                    links = valid
                    break
            except Exception:
                continue
        
        # リンククリックを試行（SPAルーティング）
        if elem_index < len(links):
            link = links[elem_index]
            try:
                WebDriverWait(self.driver, 2).until(
                    EC.element_to_be_clickable(link)
                )
                link.click()
                return
            except Exception as e:
                print(_("  Link click failed, falling back to URL: {}").format(
                    e
                ))
        
        # フォールバック: 直接URLに遷移
        if href:
            if not href.startswith('http'):
                href = "https://app.devin.ai" + href
            self.driver.get(href)
        else:
            raise Exception(
                _("Link index {} is out of range and href is not available"
                  ).format(elem_index)
            )
    
    def generate_chapter_numbers(self, sections):
        """セクションリストから階層的な章番号を生成"""
        chapter_numbers = []
        counters = [0, 0, 0, 0]  # 最大4階層まで対応
        
        for section in sections:
            level = section['level']
            
            # 現在のレベルのカウンタを増加
            counters[level] += 1
            
            # 下位レベルのカウンタをリセット
            for i in range(level + 1, len(counters)):
                counters[i] = 0
            
            # 章番号を生成（例: 01, 02, 02_01, 02_01_01）
            parts = []
            for i in range(level + 1):
                parts.append(f"{counters[i]:02d}")
            
            chapter_number = '_'.join(parts)
            chapter_numbers.append(chapter_number)
        
        return chapter_numbers
    
    def export_all(self, base_url):
        """全セクションをエクスポート"""
        print("\n" + "="*60)
        print(_("Starting DeepWiki export"))
        print("="*60)
        
        # ベースURLを保存（内部リンク変換で使用）
        self.base_url = base_url
        
        # ベースURLに移動
        self.driver.get(base_url)
        self.wait_for_page_load()
        
        # セクション一覧を取得
        sections = self.get_wiki_sections()
        
        if not sections:
            print(_("\nNo sections found. Exporting current page only."))
            self.scroll_to_load_all_content()
            html_content = self.extract_page_html()
            md_content = self.convert_html_to_markdown(html_content, 'main')
            
            md_path = os.path.join(self.output_dir, 'main.md')
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(md_content)
            
            print(_("Saved: {}").format(md_path))
            return
        
        print(_("\nDetected sections: {}").format(len(sections)))
        
        # 階層的な章番号を生成
        chapter_numbers = self.generate_chapter_numbers(sections)
        
        # 各セクションをエクスポート
        exported = []
        for i, (section, chapter_num) in enumerate(zip(sections, chapter_numbers)):
            result = self.export_section_by_click(section, chapter_num, len(sections))
            if result:
                exported.append(result)
            # レート制限対策は不要（DeepWikiはローカルクリックなので制限なし）
        
        # DeepWiki内リンクをMarkdownファイルリンクに変換
        self.convert_internal_links(exported)
        
        # 目次を生成
        self.generate_table_of_contents(exported)
        
        # PNG変換が必要な場合は一括処理
        if 'png' in self.diagram_types:
            self.process_pending_png_conversions()
        
        print("\n" + "="*60)
        print(_("Export completed!"))
        print(_("Output directory: {}").format(self.output_dir))
        print(_("Exported files: {}").format(len(exported)))
        print("="*60)
    
    def convert_internal_links(self, exported):
        """DeepWiki内リンクをMarkdownファイルリンクに変換"""
        if not exported:
            return
        
        print(_("\nConverting internal links..."))
        
        # セクション番号→ファイル名のマッピングを作成
        link_map = {}
        for idx, item in enumerate(exported, start=1):
            link_map[str(idx)] = item['filename']
            
            chapter_num = item.get('chapter_number', '')
            if '_' in chapter_num:
                parts = chapter_num.split('_')
                dotted = '.'.join(str(int(p)) for p in parts)
                link_map[dotted] = item['filename']
        
        # URL パス→ファイル名のマッピングを作成（両サイト対応）
        path_map = {}
        base_path = ''
        if hasattr(self, 'base_url'):
            from urllib.parse import urlparse
            parsed = urlparse(self.base_url)
            path_parts = parsed.path.strip('/').split('/')
            if len(path_parts) >= 2:
                base_path = '/' + '/'.join(path_parts[:2])
            
            for item in exported:
                href = item.get('href', '')
                if not href:
                    continue
                
                if href.startswith('http'):
                    p = urlparse(href)
                    path = p.path
                else:
                    path = href
                
                path = path.rstrip('/')
                path_map[path] = item['filename']
        
        # 各Markdownファイルのリンクを変換
        converted_count = 0
        for item in exported:
            md_path = os.path.join(self.output_dir, item['filename'])
            if not os.path.exists(md_path):
                continue
            
            with open(md_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            original = content
            
            # 1. 番号リンク変換: [テキスト](#番号)
            def replace_num_link(match):
                text = match.group(1)
                section_num = match.group(2)
                if section_num in link_map:
                    return f'[{text}](./{link_map[section_num]})'
                return match.group(0)
            
            pattern_num = r'\[([^\]]+)\]\(#(\d+(?:\.\d+)*)\)'
            content = re.sub(pattern_num, replace_num_link, content)
            
            # 2. パスリンク変換（両サイト対応）
            if path_map:
                def replace_path_link(match):
                    text = match.group(1)
                    href = match.group(2)
                    
                    from urllib.parse import urlparse
                    if href.startswith('http'):
                        p = urlparse(href)
                        path = p.path
                    else:
                        path = href
                    
                    path = path.rstrip('/')
                    
                    if path in path_map:
                        return f'[{text}](./{path_map[path]})'
                    return match.group(0)
                
                # deepwiki.comおよびapp.devin.aiのリンクパターン
                pattern_path = (
                    r'\[([^\]]+)\]\('
                    r'(https?://(?:deepwiki\.com|app\.devin\.ai)/[^\)]+|'
                    r'/[^\)]+)\)'
                )
                content = re.sub(pattern_path, replace_path_link, content)
            
            if content != original:
                with open(md_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                converted_count += 1
        
        if converted_count > 0:
            print(_("  Converted links in {} files").format(converted_count))
        else:
            print(_("  No links to convert"))
    
    def generate_table_of_contents(self, exported):
        """目次ファイルを生成（階層構造対応）"""
        toc_lines = [_("# Table of Contents") + "\n\n"]
        
        for item in exported:
            # 階層レベルに応じてインデントを追加
            level = item.get('level', 0)
            indent = "  " * level
            chapter_num = item.get('chapter_number', '')
            toc_lines.append(f"{indent}- [{chapter_num} {item['title']}]({item['filename']})\n")
        
        toc_path = os.path.join(self.output_dir, '00_table_of_contents.md')
        with open(toc_path, 'w', encoding='utf-8') as f:
            f.writelines(toc_lines)
        
        print(_("\nGenerated table of contents: {}").format(toc_path))
    
    def sanitize_filename(self, name):
        """ファイル名として安全な文字列に変換"""
        # 特殊文字を除去
        safe = re.sub(r'[\\/:*?"<>|]', '', name)
        safe = safe.replace(' ', '_')
        # 日本語は保持
        return safe[:50]
    
    def setup_png_browser(self):
        """PNG変換用のheadlessブラウザを起動"""
        if self.png_driver is not None:
            return  # 既に起動済み
        
        print(_("\nStarting browser for PNG conversion..."))
        options = Options()
        options.add_argument('--headless')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        # SVGが大きい場合に備えて十分なウィンドウサイズを設定
        options.add_argument('--window-size=2560,1440')
        
        if HAS_WEBDRIVER_MANAGER:
            try:
                service = Service(ChromeDriverManager().install())
                self.png_driver = webdriver.Chrome(service=service, options=options)
            except Exception as e:
                print(_("  Failed to get ChromeDriver via webdriver-manager: {}").format(e))
                self.png_driver = webdriver.Chrome(options=options)
        else:
            self.png_driver = webdriver.Chrome(options=options)
        
        # 背景を透過に設定（CDPコマンド）
        try:
            self.png_driver.execute_cdp_cmd(
                "Emulation.setDefaultBackgroundColorOverride",
                {"color": {"r": 0, "g": 0, "b": 0, "a": 0}}
            )
        except Exception as e:
            print(_("  Failed to set transparent background: {}").format(e))
    
    def _fix_svg_dimensions(self, svg_content):
        """SVGのwidth/heightがない場合、viewBoxから補完する"""
        # root <svg ...> タグを探す
        svg_match = re.search(r'<svg\b([^>]*)>', svg_content, re.IGNORECASE)
        if not svg_match:
            return svg_content
        
        attrs = svg_match.group(1)
        
        # viewBoxを取得
        viewbox_match = re.search(r'\bviewBox\s*=\s*"([^"]+)"', attrs, re.IGNORECASE)
        if not viewbox_match:
            return svg_content
        
        # viewBoxからサイズを計算
        parts = viewbox_match.group(1).split()
        if len(parts) != 4:
            return svg_content
        
        try:
            vw = float(parts[2])
            vh = float(parts[3])
            if vw <= 0 or vh <= 0:
                return svg_content
        except ValueError:
            return svg_content
        
        # width/heightの現在値を確認
        width_match = re.search(r'\bwidth\s*=\s*"([^"]+)"', attrs)
        height_match = re.search(r'\bheight\s*=\s*"([^"]+)"', attrs)
        
        # 修正が必要かチェック（無い、0、%の場合）
        need_width_fix = False
        need_height_fix = False
        
        if not width_match:
            need_width_fix = True
        else:
            w_val = width_match.group(1)
            if w_val == '0' or w_val.endswith('%'):
                need_width_fix = True
        
        if not height_match:
            need_height_fix = True
        else:
            h_val = height_match.group(1)
            if h_val == '0' or h_val.endswith('%'):
                need_height_fix = True
        
        if not need_width_fix and not need_height_fix:
            return svg_content
        
        # 属性を修正
        new_attrs = attrs
        if need_width_fix:
            if width_match:
                new_attrs = re.sub(r'\bwidth\s*=\s*"[^"]+"', f'width="{vw}"', new_attrs)
            else:
                new_attrs = new_attrs.rstrip() + f' width="{vw}"'
        
        if need_height_fix:
            if height_match:
                new_attrs = re.sub(r'\bheight\s*=\s*"[^"]+"', f'height="{vh}"', new_attrs)
            else:
                new_attrs = new_attrs.rstrip() + f' height="{vh}"'
        
        # SVGを再構築
        svg_content = (
            svg_content[:svg_match.start(1)] +
            new_attrs +
            svg_content[svg_match.end(1):]
        )
        return svg_content
    
    def _scale_svg_to_fit_viewport(self, svg_content, max_width=2400, max_height=1300):
        """SVGのサイズがビューポートを超える場合、アスペクト比を維持して縮小する
        
        ビューポートサイズ（2560x1440）より少し小さい上限を設定し、
        SVGがこの範囲に収まるようにスケーリングする。
        """
        svg_match = re.search(r'<svg\b([^>]*)>', svg_content, re.IGNORECASE)
        if not svg_match:
            return svg_content
        
        attrs = svg_match.group(1)
        
        width_match = re.search(r'\bwidth\s*=\s*"([^"]+)"', attrs)
        height_match = re.search(r'\bheight\s*=\s*"([^"]+)"', attrs)
        
        if not width_match or not height_match:
            return svg_content
        
        try:
            width_str = width_match.group(1)
            height_str = height_match.group(1)
            current_w = float(re.sub(r'[^\d.]', '', width_str))
            current_h = float(re.sub(r'[^\d.]', '', height_str))
            
            if current_w <= 0 or current_h <= 0:
                return svg_content
            
            if current_w <= max_width and current_h <= max_height:
                return svg_content
            
            scale_w = max_width / current_w
            scale_h = max_height / current_h
            scale = min(scale_w, scale_h, 1.0)
            
            if scale >= 1.0:
                return svg_content
            
            new_w = current_w * scale
            new_h = current_h * scale
            
            new_attrs = re.sub(
                r'\bwidth\s*=\s*"[^"]+"',
                f'width="{new_w:.2f}"',
                attrs
            )
            new_attrs = re.sub(
                r'\bheight\s*=\s*"[^"]+"',
                f'height="{new_h:.2f}"',
                new_attrs
            )
            
            svg_content = (
                svg_content[:svg_match.start(1)] +
                new_attrs +
                svg_content[svg_match.end(1):]
            )
            
            return svg_content
            
        except (ValueError, AttributeError):
            return svg_content
    
    def convert_svg_to_png(self, svg_path, png_path):
        """SVGファイルをブラウザでレンダリングしてPNGに変換"""
        try:
            svg_content = Path(svg_path).read_text(encoding='utf-8')
            
            svg_content = self._fix_svg_dimensions(svg_content)
            
            svg_content = self._scale_svg_to_fit_viewport(svg_content)
            
            wrapper_html = f'''<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;display:inline-block;background:transparent;">
{svg_content}
</body>
</html>'''
            
            with tempfile.NamedTemporaryFile(
                mode='w', suffix='.html', delete=False, encoding='utf-8'
            ) as tmp_file:
                tmp_file.write(wrapper_html)
                tmp_html_path = tmp_file.name
            
            try:
                file_uri = Path(tmp_html_path).as_uri()
                self.png_driver.get(file_uri)
                
                svg_elem = self.png_driver.find_element(By.TAG_NAME, 'svg')
                
                self.png_driver.execute_script(
                    "arguments[0].scrollIntoView(true);", svg_elem
                )
                time.sleep(0.2)
                
                svg_elem.screenshot(png_path)
                return True
                
            finally:
                try:
                    os.unlink(tmp_html_path)
                except:
                    pass
                    
        except Exception as e:
            print(_("  PNG conversion error ({}): {}").format(os.path.basename(svg_path), e))
            return False
    
    def process_pending_png_conversions(self):
        """保留中のPNG変換を一括処理"""
        if not self.pending_png_conversions:
            return
        
        print(_("\nConverting {} SVGs to PNG...").format(len(self.pending_png_conversions)))
        
        # PNG変換用ブラウザを起動
        self.setup_png_browser()
        
        success_count = 0
        for svg_path, png_path in self.pending_png_conversions:
            if self.convert_svg_to_png(svg_path, png_path):
                success_count += 1
        
        print(_("  PNG conversion completed: {}/{}").format(success_count, len(self.pending_png_conversions)))
        self.pending_png_conversions = []
    
    def close(self):
        """ブラウザを閉じる"""
        if self.png_driver:
            self.png_driver.quit()
        if self.driver:
            self.driver.quit()


def print_usage():
    """使用方法を表示"""
    usage_text = _("""
DeepWiki Export Tool

Usage:
    python deepwiki2md.py <DeepWiki URL> [options]

Supported sites:
    - https://deepwiki.com/owner/repo (public, no login required)
    - https://app.devin.ai/wiki/owner/repo (login required)

Options:
    --output, -o        Output directory (default: output)
    --lang, -l          Language selection (default: japanese)
                        * Language selection is disabled for deepwiki.com
                        Examples: japanese, english, chinese, korean, etc.
    --diagram_type, -d  Diagram output format (default: mermaid,svg)
                        png: PNG image only
                        svg: SVG image only
                        mermaid: Mermaid notation only
                        Multiple formats (comma-separated): png,mermaid,svg
                        First format displays inline, others collapse in details

Examples:
    # deepwiki.com (public)
    python deepwiki2md.py https://deepwiki.com/microsoft/vscode
    python deepwiki2md.py https://deepwiki.com/owner/repo -o ./output
    
    # app.devin.ai/wiki (login required)
    python deepwiki2md.py https://app.devin.ai/wiki/owner/repo
    python deepwiki2md.py https://app.devin.ai/wiki/owner/repo --lang english
    python deepwiki2md.py https://app.devin.ai/wiki/owner/repo -o ./output -l japanese
    python deepwiki2md.py https://app.devin.ai/wiki/owner/repo -d png,mermaid,svg

Required packages:
    pip install selenium beautifulsoup4 webdriver-manager

Notes:
    - Chrome/Chromium browser is required
    - app.devin.ai/wiki requires login on first run (session is preserved)
    - deepwiki.com does not require login
    - PNG output is generated by rendering SVG in browser
""")
    print(usage_text)


def main():
    """メイン関数"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description=_('DeepWiki Export Tool'),
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('url', help=_('DeepWiki URL (e.g., https://deepwiki.com/owner/repo or https://app.devin.ai/org/{org}/wiki/owner/repo)'))
    parser.add_argument('-o', '--output', default='output', help=_('Output directory (default: output)'))
    parser.add_argument('-l', '--lang', default='japanese', help=_('Language selection (default: japanese) *disabled for deepwiki.com'))
    parser.add_argument('-d', '--diagram_type', default='mermaid,svg', 
                        help=_('Diagram output format (default: mermaid,svg). Specify png/svg/mermaid comma-separated'))
    parser.add_argument('--no-headless', action='store_true',
                        help=_('Run in GUI mode (show browser window)'))
    parser.add_argument('-e', '--email', default=None,
                        help=_('Email address for login (prompts if not specified)'))
    
    args = parser.parse_args()
    
    # headlessフラグを設定（デフォルトはTrue、--no-headlessでFalse）
    args.headless = not args.no_headless
    
    # URLの検証
    if not args.url.startswith('http'):
        print(_("Error: Please specify a valid URL"))
        print_usage()
        sys.exit(1)
    
    exporter = DeepWikiExporter(args.output, diagram_types=args.diagram_type)
    
    # サイト種別を検出
    exporter.detect_site_type(args.url)
    
    try:
        # ブラウザを起動
        print(_("Starting browser..."))
        exporter.setup_browser(headless=args.headless)
        
        # ログインを待つ（deepwiki.comの場合はスキップ）
        exporter.navigate_and_wait_for_login(args.url, headless=args.headless, email=args.email)
        
        # 言語を選択（deepwiki.comの場合はスキップ）
        if args.lang and exporter.site_type == DeepWikiExporter.SITE_DEVIN:
            exporter.select_language(args.lang)
        elif exporter.site_type == DeepWikiExporter.SITE_DEEPWIKI:
            print(_("deepwiki.com: Skipping language selection"))
        
        # 全セクションをエクスポート
        exporter.export_all(args.url)
        
    except KeyboardInterrupt:
        print(_("\n\nInterrupted."))
    except Exception as e:
        print(_("\nError: {}").format(e))
        import traceback
        traceback.print_exc()
    finally:
        exporter.close()


if __name__ == '__main__':
    main()
