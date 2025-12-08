#!/usr/bin/env python3
"""DeepWiki HTMLからMarkdownに変換するスクリプト"""

import re
import os
import sys
import html
from bs4 import BeautifulSoup

# 既存のMermaid変換ロジックをインポート
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_subgraphs import extract_mermaid_from_svg

# SVG出力用のグローバル変数
_svg_counter = 0
_svg_output_dir = None
_svg_relative_path = 'images'
_svg_base_name = 'diagram'

def set_svg_output(output_dir, relative_path='images', base_name='diagram'):
    """SVG出力の設定を行う"""
    global _svg_counter, _svg_output_dir, _svg_relative_path, _svg_base_name
    _svg_counter = 0
    _svg_output_dir = output_dir
    _svg_relative_path = relative_path
    _svg_base_name = base_name

# SVGのcamelCase要素マッピング（小文字→正しいケース）
# BeautifulSoupのhtml.parserが小文字化するため、保存時に修正が必要
_SVG_CAMELCASE_TAGS = {
    'foreignobject': 'foreignObject',
    'lineargradient': 'linearGradient',
    'radialgradient': 'radialGradient',
    'clippath': 'clipPath',
    'textpath': 'textPath',
    'altglyphdef': 'altGlyphDef',
    'altglyphitem': 'altGlyphItem',
    'glyphref': 'glyphRef',
    'fegaussianblur': 'feGaussianBlur',
    'fecolormatrix': 'feColorMatrix',
    'fecomponenttransfer': 'feComponentTransfer',
    'fecomposite': 'feComposite',
    'feconvolvematrix': 'feConvolveMatrix',
    'fediffuselighting': 'feDiffuseLighting',
    'fedisplacementmap': 'feDisplacementMap',
    'fedistantlight': 'feDistantLight',
    'feflood': 'feFlood',
    'fefunca': 'feFuncA',
    'fefuncb': 'feFuncB',
    'fefuncg': 'feFuncG',
    'fefuncr': 'feFuncR',
    'feimage': 'feImage',
    'femerge': 'feMerge',
    'femergenode': 'feMergeNode',
    'femorphology': 'feMorphology',
    'feoffset': 'feOffset',
    'fepointlight': 'fePointLight',
    'fespecularlighting': 'feSpecularLighting',
    'fespotlight': 'feSpotLight',
    'fetile': 'feTile',
    'feturbulence': 'feTurbulence',
    'feblend': 'feBlend',
}

# SVGのcamelCase属性マッピング（小文字→正しいケース）
_SVG_CAMELCASE_ATTRS = {
    'viewbox': 'viewBox',
    'preserveaspectratio': 'preserveAspectRatio',
    'pathlength': 'pathLength',
    'startoffset': 'startOffset',
    'textlength': 'textLength',
    'lengthadjust': 'lengthAdjust',
    'basefrequency': 'baseFrequency',
    'numoctaves': 'numOctaves',
    'stddeviation': 'stdDeviation',
    'specularconstant': 'specularConstant',
    'specularexponent': 'specularExponent',
    'surfacescale': 'surfaceScale',
    'diffuseconstant': 'diffuseConstant',
    'kernelmatrix': 'kernelMatrix',
    'kernelunitlength': 'kernelUnitLength',
    'targetx': 'targetX',
    'targety': 'targetY',
    'edgemode': 'edgeMode',
    'filterunits': 'filterUnits',
    'primitiveunits': 'primitiveUnits',
    'gradientunits': 'gradientUnits',
    'gradienttransform': 'gradientTransform',
    'spreadmethod': 'spreadMethod',
    'markerunits': 'markerUnits',
    'markerwidth': 'markerWidth',
    'markerheight': 'markerHeight',
    'maskcontentunits': 'maskContentUnits',
    'maskunits': 'maskUnits',
    'patterncontentunits': 'patternContentUnits',
    'patternunits': 'patternUnits',
    'patterntransform': 'patternTransform',
    'clippatunits': 'clipPathUnits',
    'refx': 'refX',
    'refy': 'refY',
    'tablevalues': 'tableValues',
    'attributename': 'attributeName',
    'attributetype': 'attributeType',
    'repeatcount': 'repeatCount',
    'repeatdur': 'repeatDur',
    'calcmode': 'calcMode',
    'keysplines': 'keySplines',
    'keytimes': 'keyTimes',
}

def _fix_svg_camelcase_tags(svg_str):
    """SVGのcamelCase要素と属性を正しいケースに修正する
    
    BeautifulSoupのhtml.parserはタグ名と属性名を小文字化するため、
    SVG要素と属性の大文字小文字を修正する必要がある。
    """
    def replace_tag(match):
        # match.group(1) = "/" または ""（スラッシュの有無）
        # match.group(2) = タグ名
        slash = match.group(1)
        tag_name = match.group(2).lower()
        if tag_name in _SVG_CAMELCASE_TAGS:
            return f"<{slash}{_SVG_CAMELCASE_TAGS[tag_name]}"
        return match.group(0)
    
    def replace_attr(match):
        # match.group(1) = 属性名
        # match.group(2) = "="
        attr_name = match.group(1).lower()
        if attr_name in _SVG_CAMELCASE_ATTRS:
            return f" {_SVG_CAMELCASE_ATTRS[attr_name]}="
        return match.group(0)
    
    # 開始タグと終了タグの両方を修正
    # パターン: <tagname または </tagname
    result = re.sub(r'<(/?)([a-zA-Z]+)', replace_tag, svg_str)
    
    # 属性名を修正
    # パターン: 空白 + 属性名 + =
    result = re.sub(r' ([a-zA-Z]+)=', replace_attr, result)
    
    return result

def extract_main_content(soup):
    """メインコンテンツ領域を抽出"""
    # DeepWikiのメインコンテンツは特定のdiv構造内にある
    # h1, h2, h3, p, pre, table, ul, ol などを探す
    content_div = None
    
    # 複数の候補から探す
    for div in soup.find_all('div'):
        h1 = div.find('h1')
        if h1:
            content_div = div
            break
    
    return content_div

def convert_element_to_md(elem, depth=0):
    """HTML要素をMarkdownに変換"""
    if elem.name is None:
        # テキストノード
        text = elem.string or ''
        return text.strip()
    
    tag = elem.name.lower()
    
    # 非表示要素をスキップ
    if elem.get('devin-hidden') == 'true':
        return ''
    
    # スキップすべき要素
    skip_tags = ['style', 'script', 'nav', 'header', 'footer', 'button', 
                 'input', 'textarea', 'form', 'iframe', 'noscript']
    if tag in skip_tags:
        return ''
    
    # 特定のクラスを持つ要素をスキップ
    elem_classes = elem.get('class', [])
    skip_classes = ['edgeLabel', 'cluster-label', 'mermaidTooltip', 
                    'flowchartTitleText', 'labelBkg']
    if any(cls in elem_classes for cls in skip_classes):
        return ''
    
    result = []
    
    if tag == 'h1':
        text = elem.get_text(strip=True)
        return f"# {text}\n\n"
    
    elif tag == 'h2':
        text = elem.get_text(strip=True)
        return f"## {text}\n\n"
    
    elif tag == 'h3':
        text = elem.get_text(strip=True)
        return f"### {text}\n\n"
    
    elif tag == 'h4':
        text = elem.get_text(strip=True)
        return f"#### {text}\n\n"
    
    elif tag == 'p':
        text = process_inline(elem)
        if text.strip():
            return f"{text}\n\n"
        return ''
    
    elif tag == 'pre':
        # コードブロックまたはSVG図
        svg = elem.find('svg')
        if svg:
            return convert_svg_to_mermaid(svg)
        
        code = elem.find('code')
        if code:
            lang = code.get('data-lang', '')
            # <br>タグを改行に変換
            for br in code.find_all('br'):
                br.replace_with('\n')
            text = code.get_text()
            # ノーブレークスペースを通常のスペースに変換
            text = text.replace('\xa0', ' ')
            # 空のコードブロックはスキップ
            if not text.strip():
                return ''
            return f"```{lang}\n{text}\n```\n\n"
        
        text = elem.get_text()
        # 空のコードブロックはスキップ
        if not text.strip():
            return ''
        return f"```\n{text}\n```\n\n"
    
    elif tag == 'code':
        text = elem.get_text()
        return f"`{text}`"
    
    elif tag == 'strong' or tag == 'b':
        text = process_inline(elem)
        return f"**{text}**"
    
    elif tag == 'em' or tag == 'i':
        text = process_inline(elem)
        return f"*{text}*"
    
    elif tag == 'a':
        text = process_inline(elem)
        href = elem.get('href', '')
        if href and text:
            return f"[{text}]({href})"
        return text
    
    elif tag == 'ul':
        items = []
        for li in elem.find_all('li', recursive=False):
            item_text = process_inline(li).strip()
            items.append(f"- {item_text}")
        return '\n'.join(items) + '\n\n'
    
    elif tag == 'ol':
        items = []
        for i, li in enumerate(elem.find_all('li', recursive=False), 1):
            item_text = process_inline(li).strip()
            items.append(f"{i}. {item_text}")
        return '\n'.join(items) + '\n\n'
    
    elif tag == 'table':
        return convert_table(elem)
    
    elif tag == 'br':
        return '\n'
    
    elif tag == 'hr':
        return '---\n\n'
    
    elif tag in ['div', 'span', 'section', 'article', 'main']:
        # コンテナ要素は子要素を処理
        for child in elem.children:
            child_result = convert_element_to_md(child, depth + 1)
            if child_result:
                result.append(child_result)
        return ''.join(result)
    
    elif tag == 'svg':
        return convert_svg_to_mermaid(elem)
    
    else:
        # その他の要素はテキストを取得
        text = process_inline(elem)
        if text.strip():
            return text
        return ''
    
    return ''.join(result)

def process_inline(elem):
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
            parts.append(f"**{process_inline(child)}**")
        elif child.name in ['em', 'i']:
            parts.append(f"*{process_inline(child)}*")
        elif child.name == 'a':
            text = process_inline(child)
            href = child.get('href', '')
            if href:
                parts.append(f"[{text}]({href})")
            else:
                parts.append(text)
        elif child.name == 'br':
            parts.append('\n')
        else:
            parts.append(process_inline(child))
    
    return ''.join(parts)

def convert_table(table_elem):
    """テーブルをMarkdownに変換"""
    rows = []
    
    # ヘッダー行
    thead = table_elem.find('thead')
    if thead:
        header_row = thead.find('tr')
        if header_row:
            cells = [process_inline(th) for th in header_row.find_all(['th', 'td'])]
            rows.append('| ' + ' | '.join(cells) + ' |')
            rows.append('| ' + ' | '.join(['---'] * len(cells)) + ' |')
    
    # ボディ行
    tbody = table_elem.find('tbody')
    if tbody:
        for tr in tbody.find_all('tr'):
            cells = [process_inline(td) for td in tr.find_all(['td', 'th'])]
            rows.append('| ' + ' | '.join(cells) + ' |')
    
    if rows:
        return '\n'.join(rows) + '\n\n'
    return ''

def convert_svg_to_mermaid(svg_elem):
    """SVG要素をMermaidに変換し、SVGファイルも保存"""
    global _svg_counter, _svg_output_dir, _svg_relative_path, _svg_base_name
    
    svg_str = str(svg_elem)
    
    # アイコンSVGはスキップ（小さいwidth/height属性またはMermaid図以外）
    # width/height属性をチェック
    svg_width = svg_elem.get('width', '')
    svg_height = svg_elem.get('height', '')
    try:
        # 数値のみ抽出（"18"や"18px"など）
        w_val = float(re.sub(r'[^\d.]', '', svg_width)) if svg_width else 0
        h_val = float(re.sub(r'[^\d.]', '', svg_height)) if svg_height else 0
        if w_val > 0 and h_val > 0 and w_val < 100 and h_val < 100:
            return ''  # 小さいアイコンはスキップ
    except:
        pass
    
    # Mermaid図かどうかをチェック（id="mermaid-"で始まるか、特定のクラスを持つか）
    svg_id = svg_elem.get('id', '')
    svg_classes = svg_elem.get('class', [])
    if isinstance(svg_classes, str):
        svg_classes = svg_classes.split()
    
    # Mermaid図でない場合はスキップ（アイコンやその他のSVG）
    is_mermaid = svg_id.startswith('mermaid-') or any('mermaid' in cls.lower() for cls in svg_classes)
    has_diagram_content = bool(svg_elem.find(class_=re.compile(r'node|edge|actor|cluster|statediagram')))
    
    if not is_mermaid and not has_diagram_content:
        # viewBoxも確認
        viewbox = svg_elem.get('viewBox', '')
        if viewbox:
            parts = viewbox.split()
            if len(parts) == 4:
                try:
                    vw, vh = float(parts[2]), float(parts[3])
                    if vw < 100 and vh < 100:
                        return ''  # 小さいviewBoxのアイコンはスキップ
                except:
                    pass
    
    # SVGからstyle要素を除去
    svg_str_clean = re.sub(r'<style[^>]*>.*?</style>', '', svg_str, flags=re.DOTALL)
    
    # 空のSVG（図形要素がない）はスキップ
    # DeepWikiでスクロールされていない部分のSVGは空の<g>タグのみを含む
    if not re.search(r'<(path|rect|polygon|circle|line|text)\b', svg_str_clean):
        return ''  # 何も描かれていないSVGはスキップ
    
    result_parts = []
    
    # SVGファイルを保存（出力ディレクトリが設定されている場合）
    if _svg_output_dir:
        _svg_counter += 1
        svg_filename = f"{_svg_base_name}_{_svg_counter:02d}.svg"
        svg_path = os.path.join(_svg_output_dir, svg_filename)
        
        # SVGファイルを保存
        try:
            os.makedirs(_svg_output_dir, exist_ok=True)
            # BeautifulSoupのhtml.parserがSVGのcamelCase要素を小文字化するため修正
            # タグ名のみを対象にする（テキスト内の誤変換を防止）
            svg_str_export = _fix_svg_camelcase_tags(svg_str)
            with open(svg_path, 'w', encoding='utf-8') as f:
                f.write(svg_str_export)
            # Markdownに画像リンクを追加（detailsタグで囲む）
            result_parts.append(f"<details>\n<summary>SVG図を表示</summary>\n\n![図]({_svg_relative_path}/{svg_filename})\n\n</details>\n\n")
        except Exception as e:
            print(f"SVG保存エラー: {e}", file=sys.stderr)
    
    # Mermaid変換を試みる
    try:
        mermaid_code = extract_mermaid_from_svg(svg_str_clean)
        if mermaid_code and len(mermaid_code) > 20:
            result_parts.append(f"```mermaid\n{mermaid_code}\n```\n\n")
    except Exception as e:
        print(f"Mermaid変換エラー: {e}", file=sys.stderr)
    
    return ''.join(result_parts)

def html_to_markdown(html_content):
    """HTMLをMarkdownに変換"""
    # 前処理: SVG外のstyle要素とCSSセレクタを除去（SVG内のstyleは保持）
    # SVGを一時的に保護してから除去処理を行い、SVGを復元する
    svg_placeholder = {}
    svg_counter = [0]
    
    def save_svg(match):
        key = f"__SVG_PLACEHOLDER_{svg_counter[0]}__"
        svg_placeholder[key] = match.group(0)
        svg_counter[0] += 1
        return key
    
    # SVGを一時的にプレースホルダーに置換
    html_with_placeholders = re.sub(r'<svg[^>]*>.*?</svg>', save_svg, html_content, flags=re.DOTALL)
    
    # SVG外のstyle要素を除去
    html_with_placeholders = re.sub(r'<style[^>]*>.*?</style>', '', html_with_placeholders, flags=re.DOTALL)
    
    # SVG外のCSSセレクタパターンを除去（#mermaid-xxx{...}）
    # 注: SVGはプレースホルダーに置換されているため影響なし
    html_with_placeholders = re.sub(r'#mermaid-[a-z0-9]+\{[^}]+\}', '', html_with_placeholders)
    html_with_placeholders = re.sub(r'#mermaid-[a-z0-9]+ [^{]+\{[^}]+\}', '', html_with_placeholders)
    
    # SVGを復元
    for key, svg_content in svg_placeholder.items():
        html_with_placeholders = html_with_placeholders.replace(key, svg_content)
    
    html_content = html_with_placeholders
    
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # メインコンテンツを探す
    content = extract_main_content(soup)
    if not content:
        content = soup.body or soup
    
    # 変換
    md_parts = []
    for elem in content.children:
        result = convert_element_to_md(elem)
        if result:
            md_parts.append(result)
    
    md_content = ''.join(md_parts)
    
    # 後処理: UI要素のテキストを除去
    md_content = md_content.replace('Link copied!', '')
    # 連続する空白行を整理
    md_content = re.sub(r'\n{3,}', '\n\n', md_content)
    
    return md_content

def main():
    if len(sys.argv) < 2:
        print("使用法: python html_to_markdown.py <HTMLファイル> [出力ファイル] [SVG出力ディレクトリ] [SVGベース名]")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    svg_output_dir_arg = sys.argv[3] if len(sys.argv) > 3 else None
    svg_base_name = sys.argv[4] if len(sys.argv) > 4 else None
    
    # 出力ファイルが指定されている場合、SVG出力ディレクトリを設定
    if output_file:
        if svg_output_dir_arg:
            svg_output_dir = svg_output_dir_arg
        else:
            output_dir = os.path.dirname(output_file) or '.'
            svg_output_dir = os.path.join(output_dir, 'images')
        
        # SVGベース名が指定されていない場合、出力ファイル名から生成
        if not svg_base_name:
            svg_base_name = os.path.splitext(os.path.basename(output_file))[0]
        
        set_svg_output(svg_output_dir, 'images', svg_base_name)
    
    with open(input_file, 'r', encoding='utf-8') as f:
        html_content = f.read()
    
    md_content = html_to_markdown(html_content)
    
    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(md_content)
        print(f"変換完了: {output_file}")
    else:
        print(md_content)

if __name__ == '__main__':
    main()
