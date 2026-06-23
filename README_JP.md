# DeepWikiエクスポートツール

DeepWikiからドキュメントを取得し、Markdown形式に変換するツールです。

## 対応サイト

- **app.devin.ai/wiki** - Devin Wiki（ログイン必要、言語選択可能）
- **deepwiki.com** - DeepWiki（ログイン不要、言語選択なし）

## 機能

- Seleniumによる自動コンテンツ取得
- 2つのサイト形式を自動検出
- HTML→Markdown変換（表、コードブロック、リスト対応）
- SVG図の保存とMermaid記法への変換
  - フローチャート
  - シーケンス図
  - 状態遷移図
  - クラス図
- PNG画像出力（ブラウザでSVGをレンダリングして生成）
- 目次ファイルの自動生成
- 内部リンクの相対パス変換
- `<details>`タグ（折りたたみ）対応
- セッション保持（app.devin.ai/wikiのみ、2回目以降はログイン不要）

### 動作モード

本ツールには2つの動作モードがあります。

**CUIモード（デフォルト）**

ブラウザ画面を表示せずにバックグラウンドで動作するモードです。ログインが必要な場合は、ターミナル上でメールアドレスと認証コードを入力します。GUIを持たないサーバー環境（SSH接続など）での実行に適しています。ただし、OAuth認証（GitHub/Google等）には対応していないため、メール＋認証コード方式でのログインのみ利用可能です。

**GUIモード（--no-headless）**

ブラウザ画面を表示して動作するモードです。ログイン操作を目視で確認できるため、初回セットアップやデバッグ時に便利です。OAuth認証（GitHub/Google等）を使用する場合は、このモードを使用してください。`-e`オプションでメールアドレスを指定すると、ログインページでメールアドレスが自動入力され、Continueボタンも自動クリックされます。

## 対応プラットフォーム

- Windows
- macOS
- Linux

## 必要条件

- Python 3.10以上
- Chrome/Chromiumブラウザ
- uv（推奨）またはpip

## インストール（PyPIから - 推奨）

```bash
# pipでインストール
pip install deepwiki2md

# またはuvでインストール
uv tool install deepwiki2md
```

インストール後、`deepwiki2md`コマンドが使用可能になります：

```bash
deepwiki2md <DeepWiki URL>

# Pythonモジュールとしても実行可能
python -m deepwiki2md <DeepWiki URL>
```

## インストール（ソースから）

```bash
git clone https://github.com/matsu582/deepwiki2md.git
cd deepwiki2md
```

### uv使用（推奨）

uvがインストールされていない場合は、以下のコマンドでインストールしてください。

**Windows (PowerShell)**:
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**macOS / Linux**:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```bash
# 依存パッケージは自動的に解決・インストールされます
uv run deepwiki2md <DeepWiki URL>
```

### pip使用

```bash
# 仮想環境を作成（推奨）
python -m venv .venv

# 仮想環境を有効化
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# ソースからインストール
pip install -e .

# 実行
deepwiki2md <DeepWiki URL>
```

### インストールなしで実行（依存パッケージのみ）

パッケージ自体をインストールせず、直接実行することもできます：

```bash
# 依存パッケージのみインストール
pip install selenium beautifulsoup4 webdriver-manager

# リポジトリルートからPythonモジュールとして実行
python -m deepwiki2md <DeepWiki URL>
```

## 使用方法

### 基本的な使い方

```bash
# PyPIからインストール済み
deepwiki2md <DeepWiki URL>

# uv使用（クローンしたソースから、インストール不要）
uv run deepwiki2md <DeepWiki URL>

# Pythonモジュールとして（クローンしたソースのルートから）
python -m deepwiki2md <DeepWiki URL>
```

### オプション

| オプション             | 説明                                  | デフォルト             |
| ---------------------- | ------------------------------------- | ---------------------- |
| `-o`, `--output`       | 出力ディレクトリ                      | `output`               |
| `-l`, `--lang`         | 言語選択                              | `japanese`             |
| `-d`, `--diagram_type` | 図の出力形式                          | `mermaid,svg`          |
| `-e`, `--email`        | ログイン用メールアドレス              | なし（プロンプト表示） |
| `--no-headless`        | GUIモードで実行（ブラウザ画面を表示） | `false`                |

### 図の出力形式（--diagram_type）

- `png`: PNG画像のみ出力
- `svg`: SVG画像のみ出力
- `mermaid`: Mermaid記法のみ出力
- 複数指定可（カンマ区切り）: `png,mermaid,svg`
- 先頭の形式が直接表示、それ以外は`<details>`で折りたたみ

### 例

```bash
# app.devin.ai/wiki（ログイン必要）
deepwiki2md https://app.devin.ai/wiki/owner/repo

# deepwiki.com（ログイン不要）
deepwiki2md https://deepwiki.com/owner/repo

# 出力ディレクトリを指定
deepwiki2md https://deepwiki.com/owner/repo -o ./my_docs

# 英語で取得（app.devin.ai/wikiのみ）
deepwiki2md https://app.devin.ai/wiki/owner/repo -l english

# PNG画像のみ出力
deepwiki2md https://deepwiki.com/owner/repo -d png

# PNG優先、Mermaid・SVGは折りたたみ
deepwiki2md https://deepwiki.com/owner/repo -d png,mermaid,svg

# メールアドレスを指定して実行
deepwiki2md https://app.devin.ai/wiki/owner/repo -e user@example.com

# GUIモードで実行（ブラウザ画面を表示）
deepwiki2md https://app.devin.ai/wiki/owner/repo --no-headless
```

## 実行の流れ

### app.devin.ai/wikiの場合

1. ツールを起動するとChromeブラウザが開きます
2. ログインページが表示された場合は、手動でログインしてください
3. ログイン完了後、ターミナルでEnterキーを押してください
4. 言語選択画面で指定した言語を自動選択します
5. 言語選択はデフォルトで**Japanease**となります。日本語のDeepWikiがすでに作成済みであること。
6. ツールが自動的に全セクションを取得してMarkdownに変換します

### deepwiki.comの場合

1. ツールを起動するとChromeブラウザが開きます
2. ログイン不要で自動的にページを読み込みます
3. ツールが自動的に全セクションを取得してMarkdownに変換します

### デフォルト動作（ヘッドレスモード）

1. ツールを起動するとヘッドレスブラウザが起動します（画面表示なし）
2. ログインページを検出した場合、CUIでメールアドレスの入力を求められます
   - `-e`オプションでメールアドレスを指定した場合は、入力プロンプトをスキップします
3. メールアドレスを入力すると、認証コードがメールに送信されます
4. 認証コード入力ページを検出した場合、CUIで認証コードの入力を求められます
5. ログイン成功後、ツールが自動的に全セクションを取得してMarkdownに変換します

**注意**: ヘッドレスモードはメール＋認証コード方式のみ対応しています。OAuth（GitHub/Google等）でログインする場合は、`--no-headless`オプションを使用してGUIモードで実行してください。

## 出力ファイル

```
出力ディレクトリ/
├── 00_table_of_contents.md  # 目次
├── 01_概要.md               # 各セクションのMarkdown
├── 02_システムアーキテクチャ.md
├── ...
└── images/                  # SVG図
    ├── 01_概要_01.svg
    ├── 01_概要_02.svg
    └── ...
```

## ファイル構成

```
deepwiki2md/
├── deepwiki2md/
│   ├── __init__.py           # パッケージ初期化 / バージョン情報
│   ├── __main__.py           # python -m deepwiki2md サポート
│   ├── cli.py                # メインスクリプト（エントリポイント）
│   ├── extract_subgraphs.py  # SVG→Mermaid変換モジュール
│   ├── html_to_markdown.py   # HTML→Markdown変換モジュール
│   └── locale/               # i18n翻訳ファイル
│       └── ja/LC_MESSAGES/
├── pyproject.toml            # プロジェクト設定
├── README.md
├── README_JP.md
└── LICENSE
```

## トラブルシューティング

### ChromeDriverのエラー

webdriver-managerが自動的にChromeDriverをダウンロードしますが、
問題が発生した場合は手動でインストールしてください：

- Windows: https://chromedriver.chromium.org/downloads
- macOS: `brew install chromedriver`
- Linux: `apt install chromium-chromedriver`

### ログインセッションのリセット

セッション情報は以下の場所に保存されます：

- Windows: `%LOCALAPPDATA%\DeepWiki2Md\chrome_profile`
- macOS: `~/Library/Application Support/DeepWiki2Md/chrome_profile`
- Linux: `~/.config/deepwiki2md/chrome_profile`

問題が発生した場合は、このディレクトリを削除してください。

## 対応DeepWikiバージョン

本ツールは以下の時点のDeepWikiサイト構造に対応しています。

| サイト | 確認日 | 主な対応構造 |
| --- | --- | --- |
| deepwiki.com | 2026-06-23 | コンテンツ: `div[class*="prose-custom"]`、サイドバー: リンクベース |
| app.devin.ai/wiki | 2026-06-23 | コンテンツ: `div.prose-main`、サイドバー: `a[href*="/page/"]`（/page/X.Y形式）、言語選択: 右上「...」メニュー内 `menuitemradio`、URL: `/org/{org}/wiki/{owner}/{repo}/page/{num}` |

DeepWikiのサイト構造は予告なく変更される場合があります。動作しない場合はサイト構造の変更が原因の可能性があります。

## 注意事項

- app.devin.ai/wikiは初回実行時にログインが必要です
- deepwiki.comはログイン不要です
- 大量のページがある場合、エクスポートに時間がかかります
- ネットワーク状況によってはタイムアウトが発生する場合があります
- deepwiki.comでは言語選択オプション（`-l`）は無視されます
