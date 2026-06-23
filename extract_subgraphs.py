#!/usr/bin/env python3
"""SVGからサブグラフ対応のMermaid記法を抽出するスクリプト"""

import re
import os
import sys
import html
import gettext

# 多言語化設定
def _setup_i18n():
    """gettextによる多言語化を設定"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    locale_dir = os.path.join(script_dir, 'locale')
    lang = os.environ.get('LANG', '')
    language = 'ja' if lang.startswith('ja') else 'en'
    try:
        translation = gettext.translation(
            'deepwiki2md', localedir=locale_dir,
            languages=[language], fallback=True
        )
        return translation.gettext
    except Exception:
        return lambda x: x

_ = _setup_i18n()

def set_language(language):
    """外部から言語を切り替える"""
    global _
    script_dir = os.path.dirname(os.path.abspath(__file__))
    locale_dir = os.path.join(script_dir, 'locale')
    try:
        translation = gettext.translation(
            'deepwiki2md', localedir=locale_dir,
            languages=[language], fallback=True
        )
        _ = translation.gettext
    except Exception:
        _ = lambda x: x

def sanitize_sequence_text(text):
    """シーケンス図のメッセージテキストをサニタイズ"""
    if not text:
        return text
    # HTMLエンティティをデコード（&lt; → <, &gt; → >, &amp; → &）
    decoded = html.unescape(text)
    # 特殊文字を含む場合はダブルクォートで囲む
    special_chars = ['<', '>', '&', '(', ')', ':', '|']
    needs_quote = any(ch in decoded for ch in special_chars)
    if needs_quote:
        # 内部のダブルクォートをエスケープ
        safe = decoded.replace('"', '\\"')
        return f'"{safe}"'
    return decoded

def sanitize_mermaid_label(text):
    """Mermaid用にラベルをサニタイズ（パイプのみ全角に変換）"""
    if not text:
        return text
    # パイプはMermaidのエッジラベル区切りなので全角に変換
    # 括弧はダブルクォートで囲むことでエスケープするため変換不要
    return text.replace('|', '｜')

def cluster_y_layers(node_positions, tol=40.0):
    """Y座標をクラスタリングしてレイヤーを作成"""
    if not node_positions:
        return []
    ys = sorted({p['y'] for p in node_positions.values()})
    layers = []
    for y in ys:
        if not layers or abs(y - layers[-1]) > tol:
            layers.append(y)
        else:
            # 近い場合は平均に寄せる
            layers[-1] = (layers[-1] + y) / 2
    return layers

def assign_layer_indices(node_positions, layers):
    """各ノードにレイヤーインデックスを割り当て"""
    node_layers = {}
    for nid, pos in node_positions.items():
        y = pos['y']
        if layers:
            idx = min(range(len(layers)), key=lambda i: abs(y - layers[i]))
            node_layers[nid] = idx
    return node_layers

def infer_direction_from_layout(node_positions, edges):
    """ノード位置とエッジの関係からフローチャートの方向を推定"""
    if not node_positions or not edges:
        return "TB"  # デフォルトは縦展開
    
    # 1. Y座標の層構造からTBを検出（優先度高）
    layers = cluster_y_layers(node_positions)
    if len(layers) >= 2:
        node_layers = assign_layer_indices(node_positions, layers)
        down_count = 0
        up_count = 0
        same_count = 0
        
        for from_node, to_node in edges:
            lf = node_layers.get(from_node)
            lt = node_layers.get(to_node)
            if lf is None or lt is None:
                continue
            if lt > lf:
                down_count += 1
            elif lt < lf:
                up_count += 1
            else:
                same_count += 1
        
        total = down_count + up_count + same_count
        # 下向きエッジが60%以上ならTB
        if total > 0 and down_count / total >= 0.6:
            return "TB"
        # 上向きエッジが60%以上もTB（逆向きフロー）
        if total > 0 and up_count / total >= 0.6:
            return "TB"
    
    # 2. エッジの主方向をカウント（|dx| vs |dy|）
    vertical_count = 0
    horizontal_count = 0
    
    for from_node, to_node in edges:
        from_pos = node_positions.get(from_node)
        to_pos = node_positions.get(to_node)
        if not from_pos or not to_pos:
            continue
        
        dx = abs(to_pos['x'] - from_pos['x'])
        dy = abs(to_pos['y'] - from_pos['y'])
        
        # 1.5倍以上の差がある場合のみカウント
        if dy >= dx * 1.5:
            vertical_count += 1
        elif dx >= dy * 1.5:
            horizontal_count += 1
    
    if vertical_count > horizontal_count:
        return "TB"
    if horizontal_count > vertical_count:
        return "LR"
    
    # 3. ノード全体の広がりで判定
    xs = [p['x'] for p in node_positions.values()]
    ys = [p['y'] for p in node_positions.values()]
    width = max(xs) - min(xs) if xs else 0
    height = max(ys) - min(ys) if ys else 0
    
    if height >= width * 1.2:
        return "TB"
    elif width >= height * 1.2:
        return "LR"
    
    return "TB"  # 曖昧な場合はTBをデフォルト

def get_mermaid_shape_by_svg_type(label, svg_shape_type):
    """SVG形状タイプに応じたMermaid形状を返す"""
    safe_label = label.replace('(', '（').replace(')', '）').replace('"', "'")
    
    # SVG形状タイプからMermaid形状へのマッピング
    # cylinder: 円筒形（データベース）→ [("label")]
    # diamond: 菱形（条件分岐）→ {"label"}
    # rect: 四角形（通常ノード）→ ["label"]
    shapes = {
        'cylinder': f'[("{safe_label}")]',
        'diamond': f'{{"{safe_label}"}}',
        'rect': f'["{safe_label}"]'
    }
    return shapes.get(svg_shape_type, shapes['rect'])

def extract_clusters(svg_content):
    """SVGからクラスタ情報を抽出（属性順序非依存）"""
    clusters = []
    
    # クラスタのgタグを探す（属性順序非依存）
    g_tag_pattern = r'<g[^>]*>'
    
    for g_match in re.finditer(g_tag_pattern, svg_content):
        tag_text = g_match.group(0)
        
        # class属性にclusterが含まれるか確認（cluster-labelは除外）
        class_match = re.search(r'class="([^"]*)"', tag_text)
        if not class_match:
            continue
        class_val = class_match.group(1)
        if class_val != 'cluster':
            continue
        
        # id属性を抽出
        id_match = re.search(r'id="([^"]+)"', tag_text)
        if not id_match:
            continue
        cluster_id_raw = id_match.group(1)
        
        # このgタグの後のrect要素を探す
        start_pos = g_match.end()
        rect_pattern = r'<rect[^>]*>'
        rect_match = re.search(rect_pattern, svg_content[start_pos:start_pos+2000])
        if not rect_match:
            continue
        
        rect_text = rect_match.group(0)
        
        # rect属性を個別に抽出（属性順序非依存）
        height_match = re.search(r'height="([^"]+)"', rect_text)
        width_match = re.search(r'width="([^"]+)"', rect_text)
        y_match = re.search(r'(?<![a-z])y="([^"]+)"', rect_text)
        x_match = re.search(r'(?<![a-z])x="([^"]+)"', rect_text)
        
        if not all([height_match, width_match, y_match, x_match]):
            continue
        
        try:
            height = float(height_match.group(1))
            width = float(width_match.group(1))
            y = float(y_match.group(1))
            x = float(x_match.group(1))
        except ValueError:
            continue
        
        # クラスタIDから括弧を除去（Mermaidでパースエラーになるため）
        cluster_id = cluster_id_raw.replace('(', '').replace(')', '')
        
        # クラスタのラベルを探す（gタグの後の範囲内で）
        label_section = svg_content[start_pos:start_pos+3000]
        label_match = re.search(r'<span class="nodeLabel"[^>]*>(?:<p[^>]*>)?([^<]+)', label_section)
        label = label_match.group(1).strip() if label_match else cluster_id_raw
        
        clusters.append({
            'id': cluster_id,
            'label': label,
            'x': x, 'y': y,
            'x2': x + width, 'y2': y + height
        })
    
    clusters.sort(key=lambda c: c['y'])
    return clusters

def detect_svg_shape_type(node_content):
    """ノードのSVG要素から形状タイプを検出"""
    # polygon要素があれば菱形（条件分岐）
    if '<polygon' in node_content:
        return 'diamond'
    
    # path要素でArc（円弧）があれば円筒形（データベース）
    path_match = re.search(r'<path[^>]*d="([^"]+)"', node_content)
    if path_match:
        d_attr = path_match.group(1)
        # Arc コマンド（A または a）があれば円筒形
        if 'A' in d_attr or 'a' in d_attr:
            return 'cylinder'
    
    # それ以外は四角形
    return 'rect'

def extract_nodes_simple(svg_content):
    """SVGからノード情報を簡易抽出（形状タイプ検出付き、属性順序非依存）"""
    nodes = {}
    node_order = []
    node_positions = {}
    node_shapes = {}
    
    # 全ての<g>タグを検索し、属性を個別に抽出（順序非依存）
    g_tag_pattern = r'<g[^>]*>'
    
    for g_match in re.finditer(g_tag_pattern, svg_content):
        tag_text = g_match.group(0)
        
        # class属性にnodeが含まれるか確認
        class_match = re.search(r'class="([^"]*)"', tag_text)
        if not class_match or 'node' not in class_match.group(1):
            continue
        
        # id属性からflowchart-XXX-NNNを抽出
        id_match = re.search(r'id="flowchart-([^"]+)-(\d+)"', tag_text)
        if not id_match:
            continue
        
        node_id = id_match.group(1)
        
        # 既に処理済みならスキップ
        if node_id in nodes:
            continue
        
        # transform属性からtranslate座標を抽出
        trans_match = re.search(r'transform="translate\(([^,]+),([^)]+)\)"', tag_text)
        if trans_match:
            try:
                tx = float(trans_match.group(1))
                ty = float(trans_match.group(2))
            except ValueError:
                continue
        else:
            # transformがタグ内にない場合、前方500文字から探す
            start = g_match.start()
            before_text = svg_content[max(0, start-500):start]
            transforms = re.findall(r'transform="translate\(([^,]+),([^)]+)\)"', before_text)
            if not transforms:
                continue
            last_transform = transforms[-1]
            try:
                tx = float(last_transform[0])
                ty = float(last_transform[1])
            except ValueError:
                continue
        
        # ノードの内容を抽出
        start = g_match.start()
        node_content = extract_node_content(svg_content, start, g_match.end())
        shape_type = detect_svg_shape_type(node_content)
        
        # ラベルを抽出
        label_match = re.search(r'<span class="nodeLabel"[^>]*>(?:<p[^>]*>)?([^<]+)', node_content)
        label = label_match.group(1).strip() if label_match else node_id
        
        nodes[node_id] = label
        node_positions[node_id] = {'x': tx, 'y': ty}
        node_shapes[node_id] = shape_type
        node_order.append(node_id)
    
    return nodes, node_positions, node_order, node_shapes


def extract_node_content(svg_content, start, match_end):
    """ノードグループの内容を抽出"""
    depth = 1
    pos = match_end
    while depth > 0 and pos < len(svg_content):
        if svg_content[pos:pos+2] == '<g':
            depth += 1
        elif svg_content[pos:pos+4] == '</g>':
            depth -= 1
        pos += 1
    return svg_content[start:pos+3]

def assign_nodes_to_clusters(node_positions, clusters):
    """ノードをクラスタに割り当て"""
    node_cluster_map = {}
    
    for node_id, pos in node_positions.items():
        x, y = pos['x'], pos['y']
        
        for cluster in clusters:
            if (cluster['x'] - 50 <= x <= cluster['x2'] + 50 and 
                cluster['y'] - 50 <= y <= cluster['y2'] + 50):
                node_cluster_map[node_id] = cluster['id']
                break
    
    return node_cluster_map

def extract_edges(svg_content, nodes):
    """SVGからエッジ情報を抽出（インデックスベースのラベルマッピング対応）"""
    edges = []
    edge_indices = []
    edge_pattern = r'id="L[-_]([^-_]+)[-_]([^-_]+)[-_](\d+)"'
    
    for match in re.finditer(edge_pattern, svg_content):
        from_node = match.group(1)
        to_node = match.group(2)
        idx = int(match.group(3))
        if from_node in nodes and to_node in nodes:
            edges.append((from_node, to_node))
            edge_indices.append(idx)
    
    # 全edgeLabelグループを抽出（空も含む）
    # これによりインデックスの対応を維持
    edge_labels = []
    edge_label_group_pattern = r'<g[^>]*class="edgeLabel"[^>]*>(.*?)</g>'
    for match in re.finditer(edge_label_group_pattern, svg_content, re.DOTALL):
        content = match.group(1)
        text_match = re.search(r'<span class="edgeLabel"[^>]*>(?:<p[^>]*>)?([^<]*)', content)
        label = text_match.group(1).strip() if text_match else ''
        edge_labels.append(label)
    
    return edges, edge_labels, edge_indices

def generate_mermaid_flowchart(nodes, node_order, clusters, node_cluster_map, edges, edge_labels, edge_indices, node_shapes, direction="TB"):
    """Mermaidフローチャートを生成（SVG形状タイプ対応、インデックスベースラベルマッピング、方向指定対応）"""
    lines = [f'flowchart {direction}', '']
    
    if clusters and node_cluster_map:
        # クラスタごとにノードをグループ化
        cluster_nodes = {c['id']: [] for c in clusters}
        unassigned_nodes = []
        
        for node_id in node_order:
            if node_id in node_cluster_map:
                cluster_id = node_cluster_map[node_id]
                cluster_nodes[cluster_id].append(node_id)
            else:
                unassigned_nodes.append(node_id)
        
        # サブグラフを出力（ダブルクォートで囲んで括弧をエスケープ）
        for cluster in clusters:
            cluster_id = cluster['id']
            # ラベル内のダブルクォートをシングルクォートに変換
            cluster_label = cluster['label'].replace('"', "'")
            
            if cluster_nodes[cluster_id]:
                lines.append(f'    subgraph "{cluster_label}"')
                for node_id in cluster_nodes[cluster_id]:
                    label = nodes[node_id].replace('\n', ' ').strip()
                    shape_type = node_shapes.get(node_id, 'rect')
                    shape = get_mermaid_shape_by_svg_type(label, shape_type)
                    lines.append(f'        {node_id}{shape}')
                lines.append('    end')
                lines.append('')
        
        # 未割り当てノード
        if unassigned_nodes:
            lines.append('%% その他のノード')
            for node_id in unassigned_nodes:
                label = nodes[node_id].replace('\n', ' ').strip()
                shape_type = node_shapes.get(node_id, 'rect')
                shape = get_mermaid_shape_by_svg_type(label, shape_type)
                lines.append(f'    {node_id}{shape}')
            lines.append('')
    else:
        # サブグラフなし
        lines.append('%% ノード')
        for node_id in node_order:
            label = nodes[node_id].replace('\n', ' ').strip()
            shape_type = node_shapes.get(node_id, 'rect')
            shape = get_mermaid_shape_by_svg_type(label, shape_type)
            lines.append(f'    {node_id}{shape}')
        lines.append('')
    
    # エッジを出力（ダブルクォートで囲んで括弧をエスケープ）
    lines.append('%% エッジ')
    for j, (from_node, to_node) in enumerate(edges):
        edge_idx = edge_indices[j] if j < len(edge_indices) else -1
        raw_label = edge_labels[edge_idx] if 0 <= edge_idx < len(edge_labels) else ''
        label = sanitize_mermaid_label(raw_label)
        if label:
            # ダブルクォートで囲んで括弧をエスケープ
            safe_label = label.replace('"', "'")
            lines.append(f'    {from_node} -->|"{safe_label}"| {to_node}')
        else:
            lines.append(f'    {from_node} --> {to_node}')
    
    return '\n'.join(lines)

def extract_root_groups(svg_content):
    """SVGから複数のルートグループとそのtranslateオフセットを抽出"""
    root_groups = []
    # ルートグループのパターン（transform属性の位置が変わる可能性に対応）
    root_pattern = r'<g[^>]*class="root"[^>]*>'
    
    for match in re.finditer(root_pattern, svg_content):
        tag_text = match.group(0)
        # translateを抽出
        trans_match = re.search(r'transform="translate\(([^,]+),\s*([^)]+)\)"', tag_text)
        offset_x, offset_y = 0.0, 0.0
        if trans_match:
            offset_x = float(trans_match.group(1))
            offset_y = float(trans_match.group(2))
        
        # ルートグループの内容を抽出（次の</g>まで）
        start_pos = match.start()
        depth = 1
        pos = match.end()
        while depth > 0 and pos < len(svg_content):
            if svg_content[pos:pos+2] == '<g':
                depth += 1
            elif svg_content[pos:pos+4] == '</g>':
                depth -= 1
            pos += 1
        inner_content = svg_content[match.end():pos-1]
        
        root_groups.append({
            'offset_x': offset_x,
            'offset_y': offset_y,
            'content': inner_content
        })
    
    return root_groups

def extract_mermaid_from_svg(svg_content):
    """SVGからMermaid記法を抽出（複数ルートグループ対応、方向自動判定）"""
    
    # 状態遷移図の検出
    if 'class="statediagram"' in svg_content.lower() or 'id="state-' in svg_content:
        return extract_state_diagram(svg_content)
    
    # シーケンス図の検出
    if 'class="messageText"' in svg_content or 'class="messageLine' in svg_content:
        return extract_sequence_diagram(svg_content)
    
    # クラス図の検出
    if 'id="classId-' in svg_content:
        return extract_class_diagram(svg_content)
    
    # 複数ルートグループがあるか確認
    root_groups = extract_root_groups(svg_content)
    
    # 複数ルートグループがある場合、親コンテナ（全ノードを含む最大のグループ）をスキップ
    # 各グループのノード数を確認し、最大のものを親コンテナとみなす
    if len(root_groups) > 1:
        # 各グループのノード数をカウント
        group_node_counts = []
        for rg in root_groups:
            nodes, _, _, _ = extract_nodes_simple(rg['content'])
            group_node_counts.append(len(nodes))
        
        max_count = max(group_node_counts)
        other_counts = [c for c in group_node_counts if c != max_count]
        other_total = sum(other_counts) if other_counts else 0
        
        # 最大ノード数を持つグループが1つだけで、他のグループの合計と同じかそれ以上なら親コンテナ
        max_indices = [i for i, c in enumerate(group_node_counts) if c == max_count]
        
        if len(max_indices) == 1 and max_count >= other_total and other_total > 0:
            # 親コンテナをスキップ
            filtered_groups = [rg for i, rg in enumerate(root_groups) if i != max_indices[0]]
        else:
            filtered_groups = root_groups
    else:
        filtered_groups = root_groups
    
    if len(filtered_groups) > 1:
        # 複数ルートグループの場合、各グループを個別に処理
        all_clusters = []
        all_nodes = {}
        all_node_positions = {}
        all_node_order = []
        all_node_shapes = {}
        all_edges = []
        all_edge_labels = []
        all_edge_indices = []
        
        for rg in filtered_groups:
            ox, oy = rg['offset_x'], rg['offset_y']
            inner = rg['content']
            
            # このルートグループ内のクラスタを抽出
            clusters = extract_clusters(inner)
            for c in clusters:
                c['x'] += ox
                c['y'] += oy
                c['x2'] += ox
                c['y2'] += oy
                all_clusters.append(c)
            
            # このルートグループ内のノードを抽出
            nodes, positions, order, shapes = extract_nodes_simple(inner)
            for nid in order:
                if nid not in all_nodes:
                    all_nodes[nid] = nodes[nid]
                    pos = positions[nid]
                    all_node_positions[nid] = {'x': pos['x'] + ox, 'y': pos['y'] + oy}
                    all_node_shapes[nid] = shapes.get(nid, 'rect')
                    all_node_order.append(nid)
            
            # このルートグループ内のエッジを抽出
            edges, labels, indices = extract_edges(inner, all_nodes)
            base_idx = len(all_edge_labels)
            for e in edges:
                all_edges.append(e)
            for lbl in labels:
                all_edge_labels.append(lbl)
            for idx in indices:
                all_edge_indices.append(idx + base_idx)
        
        if not all_nodes:
            return None
        
        # ノード・エッジ抽出後に方向を判定
        direction = infer_direction_from_layout(all_node_positions, all_edges)
        
        node_cluster_map = assign_nodes_to_clusters(all_node_positions, all_clusters)
        return generate_mermaid_flowchart(
            all_nodes, all_node_order, all_clusters, node_cluster_map,
            all_edges, all_edge_labels, all_edge_indices, all_node_shapes, direction
        )
    
    # 単一ルートまたはルートなしの場合は従来の処理
    clusters = extract_clusters(svg_content)
    nodes, node_positions, node_order, node_shapes = extract_nodes_simple(svg_content)
    
    if not nodes:
        return None
    
    node_cluster_map = assign_nodes_to_clusters(node_positions, clusters) if clusters else {}
    edges, edge_labels, edge_indices = extract_edges(svg_content, nodes)
    
    # ノード・エッジ抽出後に方向を判定
    direction = infer_direction_from_layout(node_positions, edges)
    
    return generate_mermaid_flowchart(nodes, node_order, clusters, node_cluster_map, edges, edge_labels, edge_indices, node_shapes, direction)

def extract_sequence_diagram(svg_content):
    """シーケンス図を抽出（参加者、メッセージ、矢印、loop/alt/optを正確に抽出）"""
    
    # 参加者（actor）を抽出（属性順序非依存）
    # まずrect要素からname属性を持つactorを探す（新しいHTML形式）
    actor_rect_pattern = r'<rect[^>]*class="actor[^"]*"[^>]*>'
    actors = {}  # {name: x} - 内部ID用
    actor_x_coords = {}  # {x: name} - x座標からIDを逆引き
    for m in re.finditer(actor_rect_pattern, svg_content):
        tag_text = m.group(0)
        
        # name属性とx属性を個別に抽出
        name_match = re.search(r'name="([^"]+)"', tag_text)
        x_match = re.search(r'(?<![a-z])x="([^"]+)"', tag_text)
        
        if name_match and x_match:
            name = name_match.group(1)
            try:
                x = float(x_match.group(1))
                # 重複を避け、角括弧を含まない名前のみ
                if '[' not in name and name not in actors:
                    actors[name] = x
                    actor_x_coords[x] = name
            except ValueError:
                continue
    
    # rect要素で見つからない場合はtext要素を試す（従来のHTML形式）
    if not actors:
        actor_text_pattern = r'<text[^>]*class="actor[^"]*"[^>]*>'
        for m in re.finditer(actor_text_pattern, svg_content):
            tag_text = m.group(0)
            
            name_match = re.search(r'name="([^"]+)"', tag_text)
            x_match = re.search(r'(?<![a-z])x="([^"]+)"', tag_text)
            
            if name_match and x_match:
                name = name_match.group(1)
                try:
                    x = float(x_match.group(1))
                    if '[' not in name and name not in actors:
                        actors[name] = x
                        actor_x_coords[x] = name
                except ValueError:
                    continue
    
    if not actors:
        return None
    
    # text.actor-boxのtspanから表示ラベルを抽出
    actor_labels = {}  # {name: label} - 表示ラベル
    actor_box_pattern = r'<text[^>]*class="actor actor-box"[^>]*x="([^"]+)"[^>]*>.*?<tspan[^>]*>([^<]*)</tspan>'
    for m in re.finditer(actor_box_pattern, svg_content, re.DOTALL):
        try:
            x = float(m.group(1))
            label = m.group(2).strip()
            # x座標が近いactorを探す（rectのx座標とtextのx座標は約75-106の差がある）
            for actor_x, actor_name in actor_x_coords.items():
                if abs(actor_x - x) < 120 and actor_name not in actor_labels:
                    actor_labels[actor_name] = label
                    break
        except ValueError:
            continue
    
    # X座標でソートした参加者リスト
    sorted_actors = sorted(actors.items(), key=lambda x: x[1])
    
    # loop/alt/optブロックを抽出（属性順序非依存）
    # loopLineからブロックの範囲を特定
    blocks = []
    loop_line_tag_pattern = r'<line[^>]*class="loopLine"[^>]*>'
    loop_lines = []
    for m in re.finditer(loop_line_tag_pattern, svg_content):
        tag_text = m.group(0)
        y1_match = re.search(r'y1="([^"]+)"', tag_text)
        y2_match = re.search(r'y2="([^"]+)"', tag_text)
        if y1_match and y2_match:
            loop_lines.append({'y1': y1_match.group(1), 'y2': y2_match.group(1)})
    
    # labelText（loop/alt/opt）を抽出（属性順序非依存）
    # タグ全体を直接マッチさせる
    label_full_pattern = r'<text[^>]*class="labelText"[^>]*>([^<]+)</text>'
    for m in re.finditer(label_full_pattern, svg_content):
        tag_text = m.group(0)  # タグ全体
        
        # タグ内のy属性を抽出（タグの開始から>までの範囲内で）
        tag_attrs = tag_text[:tag_text.find('>')]
        y_match = re.search(r'(?<![a-z])y="([^"]+)"', tag_attrs)
        if not y_match:
            continue
        try:
            label_y = float(y_match.group(1))
        except ValueError:
            continue
        label_text = m.group(1).strip().lower()
        
        # loopLineからブロックの範囲を特定
        # y1がlabel_yに近く、y2がy1と異なる（垂直線）ものを探す
        block_y1 = label_y - 20  # ラベルの少し上
        block_y2 = None
        
        for line_data in loop_lines:
            try:
                y2 = float(line_data['y2'])
                y1 = float(line_data['y1'])
            except ValueError:
                continue
            # ラベルに近いloopLineで、y1とy2が異なるもの（垂直線）を探す
            if abs(y1 - label_y) < 30 and abs(y2 - y1) > 50:
                block_y2 = y2
                break
        
        if block_y2:
            blocks.append({
                'type': label_text,
                'y1': block_y1,
                'y2': block_y2,
                'label_y': label_y,
                'condition': "",
                'conditions': []
            })
    
    # ブロックをY座標でソート
    blocks.sort(key=lambda b: b['y1'])
    
    # 全loopText（条件）を抽出
    all_conditions = []
    # tspanありのパターン
    loop_text_pattern1 = r'<text[^>]*class="loopText"[^>]*y="([^"]+)"[^>]*>.*?<tspan[^>]*>([^<]+)</tspan>'
    for lt_m in re.finditer(loop_text_pattern1, svg_content, re.DOTALL):
        all_conditions.append({
            'y': float(lt_m.group(1)),
            'text': lt_m.group(2).strip()
        })
    # tspanなしのパターン（直接テキストが入っている場合）
    loop_text_pattern2 = r'<text[^>]*class="loopText"[^>]*y="([^"]+)"[^>]*>(\[[^\]]+\])</text>'
    for lt_m in re.finditer(loop_text_pattern2, svg_content, re.DOTALL):
        lt_y = float(lt_m.group(1))
        lt_text = lt_m.group(2).strip()
        # 重複チェック（同じY座標の条件は追加しない）
        if not any(c['y'] == lt_y for c in all_conditions):
            all_conditions.append({
                'y': lt_y,
                'text': lt_text
            })
    
    # 各条件を最も近いブロックに割り当て（ブロックの範囲内かつlabel_yに最も近い）
    for cond in all_conditions:
        best_block = None
        best_dist = float('inf')
        for b in blocks:
            # 条件がブロックの範囲内にあるか確認
            if b['y1'] - 10 <= cond['y'] <= b['y2'] + 10:
                # label_yとの距離を計算
                dist = abs(cond['y'] - b['label_y'])
                if dist < best_dist:
                    best_dist = dist
                    best_block = b
        if best_block:
            best_block['conditions'].append(cond)
    
    # 各ブロックの条件をY座標でソートし、最初の条件をconditionに設定
    for b in blocks:
        b['conditions'].sort(key=lambda c: c['y'])
        if b['conditions']:
            b['condition'] = b['conditions'][0]['text']
    
    # line要素（通常の矢印）を抽出（属性順序非依存）
    line_tag_pattern = r'<line[^>]*class="messageLine(\d)"[^>]*>'
    arrows = []
    for m in re.finditer(line_tag_pattern, svg_content):
        tag_text = m.group(0)
        line_type = int(m.group(1))
        
        # 各属性を個別に抽出
        x1_match = re.search(r'x1="([^"]+)"', tag_text)
        x2_match = re.search(r'x2="([^"]+)"', tag_text)
        y1_match = re.search(r'y1="([^"]+)"', tag_text)
        y2_match = re.search(r'y2="([^"]+)"', tag_text)
        
        if all([x1_match, x2_match, y1_match, y2_match]):
            try:
                arrows.append({
                    'type': 'line',
                    'line_type': line_type,
                    'y': float(y2_match.group(1)),
                    'x1': float(x1_match.group(1)),
                    'x2': float(x2_match.group(1))
                })
            except ValueError:
                continue
    
    # path要素（自己ループ）を抽出
    path_pattern = r'<path[^>]*class="messageLine(\d)"[^>]*d="M\s*([^,]+),([^\s]+)\s+C[^"]*"'
    for m in re.finditer(path_pattern, svg_content):
        x = float(m.group(2))
        y = float(m.group(3))
        arrows.append({
            'type': 'path',
            'line_type': int(m.group(1)),
            'y': y,
            'x1': x,
            'x2': x  # 自己ループなので同じ
        })
    
    # メッセージテキストを抽出（属性順序非依存、タグ内から直接y座標を取得）
    messages = []
    msg_text_pattern = r'<text[^>]*class="messageText"[^>]*>[^<]+</text>'
    for m in re.finditer(msg_text_pattern, svg_content):
        tag_text = m.group(0)  # マッチしたタグ全体
        tag_attrs = tag_text[:tag_text.find('>')]  # 属性部分のみ
        
        # y属性とx属性を抽出（タグ内から直接）
        y_match = re.search(r'(?<![a-z])y="([^"]+)"', tag_attrs)
        x_match = re.search(r'(?<![a-z])x="([^"]+)"', tag_attrs)
        
        # テキスト部分を抽出
        text_match = re.search(r'>([^<]+)</text>', tag_text)
        
        if y_match and x_match and text_match:
            try:
                y_val = float(y_match.group(1))
                x_val = float(x_match.group(1))
                messages.append({
                    'y': y_val,
                    'x': x_val,
                    'text': text_match.group(1).strip(),
                    'is_note': False
                })
            except ValueError:
                continue
    
    # noteTextクラスの要素を抽出（フェーズ説明等のNote over、タグ内から直接y座標を取得）
    note_text_pattern = r'<text[^>]*class="noteText"[^>]*>.*?<tspan[^>]*>([^<]+)</tspan>'
    for m in re.finditer(note_text_pattern, svg_content, re.DOTALL):
        tag_text = m.group(0)  # マッチしたタグ全体
        # textタグの属性部分を抽出（最初の>まで）
        first_close = tag_text.find('>')
        tag_attrs = tag_text[:first_close] if first_close > 0 else tag_text
        
        # y属性とx属性を個別に抽出（タグ内から直接）
        y_match = re.search(r'(?<![a-z])y="([^"]+)"', tag_attrs)
        x_match = re.search(r'(?<![a-z])x="([^"]+)"', tag_attrs)
        
        if y_match and x_match:
            try:
                y_val = float(y_match.group(1))
                x_val = float(x_match.group(1))
                messages.append({
                    'y': y_val,
                    'x': x_val,
                    'text': m.group(1).strip(),
                    'is_note': True
                })
            except ValueError:
                continue
    
    # note rect要素を抽出（Note overの範囲を示す）
    note_rects = []
    note_rect_pattern = r'<rect[^>]*class="note"[^>]*height="([^"]+)"[^>]*width="([^"]+)"[^>]*stroke[^>]*fill[^>]*y="([^"]+)"[^>]*x="([^"]+)"'
    for m in re.finditer(note_rect_pattern, svg_content):
        h = float(m.group(1))
        w = float(m.group(2))
        y = float(m.group(3))
        x = float(m.group(4))
        center_y = y + h / 2
        note_rects.append({'y': center_y, 'x': x, 'width': w})
    
    if not messages:
        return None
    
    # Y座標でソート
    arrows.sort(key=lambda a: a['y'])
    messages.sort(key=lambda m: m['y'])
    
    # X座標から参加者を特定するヘルパー関数
    def find_actor(x):
        closest = None
        min_dist = float('inf')
        for name, ax in sorted_actors:
            # 参加者の中心に調整（width=150の半分）
            dist = abs(x - (ax + 75))
            if dist < min_dist:
                min_dist = dist
                closest = name
        return closest
    
    # Note overの範囲（開始・終了参加者）を取得
    def find_note_range(note_y):
        """note rectからNote overの範囲を取得"""
        best_rect = None
        best_dist = float('inf')
        for rect in note_rects:
            dist = abs(rect['y'] - note_y)
            if dist < best_dist and dist < 30:
                best_dist = dist
                best_rect = rect
        
        if not best_rect:
            return None, None
        
        # rectの範囲内にある参加者を特定
        x_start = best_rect['x']
        x_end = x_start + best_rect['width']
        covered = []
        for name, ax in sorted_actors:
            cx = ax + 75  # 参加者の中心
            if x_start <= cx <= x_end:
                covered.append(name)
        
        if covered:
            return covered[0], covered[-1]
        return None, None
    
    # Mermaidシーケンス図を生成
    mermaid_lines = ['sequenceDiagram']
    
    # 参加者を宣言（表示ラベルがある場合は "participant ID as Label" 形式）
    for name, _ in sorted_actors:
        label = actor_labels.get(name, name)
        # ラベルにMermaidで問題になる文字がある場合はサニタイズ
        safe_label = sanitize_sequence_text(label)
        if safe_label != name:
            mermaid_lines.append(f'    participant {name} as {safe_label}')
        else:
            mermaid_lines.append(f'    participant {name}')
    mermaid_lines.append('')
    
    # Y座標に対してアクティブなブロック一覧を取得（外側→内側の順）
    def get_blocks_for_y(y):
        """Y座標を含む全ブロックを外側→内側の順で返す"""
        result = [b for b in blocks if b['y1'] <= y <= b['y2']]
        # y1が小さいほど外側なので、y1でソート
        result.sort(key=lambda b: b['y1'])
        return result
    
    # メッセージと矢印をマッチングし、出力用リストを作成
    output_items = []
    used_arrows = set()
    
    for msg in messages:
        item = {'y': msg['y'], 'type': 'message'}
        
        # noteTextクラスの要素は常にNote overとして出力（範囲付き）
        if msg.get('is_note', False):
            start_actor, end_actor = find_note_range(msg['y'])
            safe_note = sanitize_sequence_text(msg["text"])
            if start_actor and end_actor and start_actor != end_actor:
                item['line'] = f'Note over {start_actor},{end_actor}: {safe_note}'
            else:
                nearest_actor = find_actor(msg['x'])
                item['line'] = f'Note over {nearest_actor}: {safe_note}'
        else:
            # 通常のメッセージは矢印とマッチング（閾値を調整）
            best_arrow = None
            best_dist = float('inf')
            
            for i, arrow in enumerate(arrows):
                if i in used_arrows:
                    continue
                # 矢印のy座標とメッセージのy座標の差を計算（閾値を緩和）
                dist = abs(arrow['y'] - msg['y'])
                if dist < best_dist and dist < 60:
                    best_dist = dist
                    best_arrow = (i, arrow)
            
            if best_arrow:
                i, arrow = best_arrow
                used_arrows.add(i)
                from_actor = find_actor(arrow['x1'])
                to_actor = find_actor(arrow['x2'])
                arrow_str = '-->>' if arrow['line_type'] == 1 else '->>'
                safe_text = sanitize_sequence_text(msg["text"])
                item['line'] = f'{from_actor} {arrow_str} {to_actor}: {safe_text}'
            else:
                # 矢印にマッチしなかったmessageTextはスキップ（Note overにしない）
                continue
        
        output_items.append(item)
    
    # ブロックの開始・終了を挿入しながら出力（ネスト対応）
    active_blocks = []
    # else分岐の出力済みインデックスを追跡（ブロックID -> 出力済み条件インデックス）
    block_condition_idx = {}
    
    for item in output_items:
        y = item['y']
        desired_blocks = get_blocks_for_y(y)  # この行でアクティブなブロック列（外→内）
        
        # 共通プレフィックス長を計算（既に開いていて引き続き有効なブロック）
        prefix_len = 0
        for i, (b_active, b_desired) in enumerate(zip(active_blocks, desired_blocks)):
            if b_active is b_desired:
                prefix_len = i + 1
            else:
                break
        
        # 余分なブロックを閉じる（内側から）
        while len(active_blocks) > prefix_len:
            active_blocks.pop()
            mermaid_lines.append('    end')
        
        # 新しく必要なブロックを外側→内側の順で開く
        for b in desired_blocks[prefix_len:]:
            block_type = b['type']
            condition = sanitize_sequence_text(b['condition'])
            indent = '    ' * (len(active_blocks) + 1)
            mermaid_lines.append(f'{indent}{block_type} {condition}')
            active_blocks.append(b)
            # 条件インデックスを初期化（最初の条件は既に出力済み）
            block_condition_idx[id(b)] = 0
        
        # altブロック内でelse分岐をチェック
        for b in active_blocks:
            if b['type'] == 'alt' and 'conditions' in b:
                conditions = b['conditions']
                current_idx = block_condition_idx.get(id(b), 0)
                # 現在のY座標が次の条件のY座標を超えたらelseを出力
                for cond_idx in range(current_idx + 1, len(conditions)):
                    cond = conditions[cond_idx]
                    if y >= cond['y'] - 20:  # 条件のY座標の少し上から
                        indent = '    ' * (active_blocks.index(b) + 1)
                        else_cond = sanitize_sequence_text(cond['text'])
                        mermaid_lines.append(f'{indent}else {else_cond}')
                        block_condition_idx[id(b)] = cond_idx
                    else:
                        break
        
        # メッセージを出力
        indent = '    ' * (len(active_blocks) + 1)
        mermaid_lines.append(f'{indent}{item["line"]}')
    
    # 残りのブロックを閉じる
    while active_blocks:
        active_blocks.pop()
        mermaid_lines.append('    end')
    
    return '\n'.join(mermaid_lines)

def extract_state_diagram(svg_content):
    """状態遷移図を抽出（状態ノード、遷移、ラベルを正確に抽出）"""
    
    # 状態ノードを抽出（属性順序非依存）
    states = {}  # {state_id: {'label': label, 'x': x, 'y': y}}
    # g要素を探し、id="state-XXX"とtransform属性を個別に抽出
    g_tag_pattern = r'<g[^>]*>'
    
    for g_match in re.finditer(g_tag_pattern, svg_content):
        tag_text = g_match.group(0)
        
        # id属性からstate-XXXを抽出
        id_match = re.search(r'id="(state-[^"]+)"', tag_text)
        if not id_match:
            continue
        state_id = id_match.group(1)
        
        # 既に処理済みならスキップ
        if state_id in states:
            continue
        
        # transform属性から座標を抽出
        trans_match = re.search(r'transform="translate\(([^,]+),\s*([^)]+)\)"', tag_text)
        if not trans_match:
            continue
        
        try:
            x = float(trans_match.group(1))
            y = float(trans_match.group(2))
        except ValueError:
            continue
        
        # 状態名を抽出（state-XXX-N形式からXXXを取得）
        name_match = re.match(r'state-([^-]+(?:-[^-]+)?)-\d+', state_id)
        if name_match:
            state_name = name_match.group(1)
        else:
            state_name = state_id.replace('state-', '')
        
        # ラベルを取得（foreignObjectまたはspan内のテキスト）
        # このstate-idの後のコンテンツを探す
        start_pos = g_match.end()
        section = svg_content[start_pos:start_pos+2000]
        
        label = state_name
        # foreignObject内のspanからラベルを取得
        label_match = re.search(r'<span[^>]*class="nodeLabel"[^>]*>(?:<p[^>]*>)?([^<]+)', section)
        if label_match:
            label = label_match.group(1).strip()
        
        # 開始・終了状態の特別処理
        if 'root_start' in state_id:
            label = '[*]'
            state_name = '_start_'
        elif 'root_end' in state_id:
            label = '[*]'
            state_name = '_end_'
        
        states[state_id] = {
            'name': state_name,
            'label': label,
            'x': x,
            'y': y
        }
    
    if not states:
        return None
    
    # 遷移パスを抽出（edge ID付き）
    transitions = []  # {'edge_id': int, 'start_x': float, ...}
    path_pattern = r'<path[^>]*class="[^"]*transition[^"]*"[^>]*>'
    
    for m in re.finditer(path_pattern, svg_content):
        path_tag = m.group(0)
        
        # edge IDを抽出
        edge_id_match = re.search(r'id="edge(\d+)"', path_tag)
        edge_id = int(edge_id_match.group(1)) if edge_id_match else -1
        
        # d属性を抽出
        d_match = re.search(r'd="([^"]+)"', path_tag)
        if not d_match:
            continue
        d_attr = d_match.group(1)
        
        # 始点を取得（M コマンド）
        start_match = re.search(r'M([\d.]+),([\d.]+)', d_attr)
        if not start_match:
            continue
        start_x = float(start_match.group(1))
        start_y = float(start_match.group(2))
        
        # 終点を取得（最後の座標）
        coords = re.findall(r'[LCQT]([\d.]+),([\d.]+)', d_attr)
        if not coords:
            continue
        end_x = float(coords[-1][0])
        end_y = float(coords[-1][1])
        
        transitions.append({
            'edge_id': edge_id,
            'start_x': start_x,
            'start_y': start_y,
            'end_x': end_x,
            'end_y': end_y
        })
    
    # 遷移ラベルを抽出（インデックス付き）
    edge_labels = []  # {'idx': int, 'label': str, 'x': float, 'y': float}
    edge_label_pattern = r'<g[^>]*class="[^"]*edgeLabel[^"]*"[^>]*>'
    
    for idx, m in enumerate(re.finditer(edge_label_pattern, svg_content)):
        tag_text = m.group(0)
        
        # transform属性から座標を抽出
        trans_match = re.search(r'transform="translate\(([^,]+),\s*([^)]+)\)"', tag_text)
        if trans_match:
            try:
                lbl_x = float(trans_match.group(1))
                lbl_y = float(trans_match.group(2))
            except ValueError:
                lbl_x, lbl_y = 0, 0
        else:
            lbl_x, lbl_y = 0, 0
        
        start_pos = m.end()
        section = svg_content[start_pos:start_pos+1000]
        
        # foreignObject内のテキストを取得
        label_match = re.search(r'<span[^>]*>(?:<p[^>]*>)?([^<]+)', section)
        label = label_match.group(1).strip() if label_match else ''
        
        edge_labels.append({'idx': idx, 'label': label, 'x': lbl_x, 'y': lbl_y})
    
    # 座標から状態間の接続を推測
    def find_nearest_state(x, y, exclude_id=None):
        """指定座標に最も近い状態を見つける"""
        min_dist = float('inf')
        nearest = None
        for sid, s in states.items():
            if sid == exclude_id:
                continue
            dist = ((s['x'] - x) ** 2 + (s['y'] - y) ** 2) ** 0.5
            if dist < min_dist:
                min_dist = dist
                nearest = sid
        return nearest, min_dist
    
    # 点から線分への最短距離を計算
    def point_to_segment_dist(px, py, x1, y1, x2, y2):
        """点(px,py)から線分(x1,y1)-(x2,y2)への最短距離"""
        dx = x2 - x1
        dy = y2 - y1
        seg_len2 = dx*dx + dy*dy
        if seg_len2 == 0:
            return ((px - x1)**2 + (py - y1)**2) ** 0.5
        
        # 射影パラメータtを計算（0〜1にクランプ）
        t = ((px - x1) * dx + (py - y1) * dy) / seg_len2
        t = max(0, min(1, t))
        
        # 最近接点
        proj_x = x1 + t * dx
        proj_y = y1 + t * dy
        
        return ((px - proj_x)**2 + (py - proj_y)**2) ** 0.5
    
    # edge IDからラベルインデックスへのマッピングを作成
    # Mermaid SVGでは edge ID + 1 = edgeLabel index の対応関係がある
    edge_id_to_label = {}
    for lbl in edge_labels:
        # 有効なラベル（座標が0,0でなく、テキストがあるもの）のみ
        if (lbl['x'] != 0 or lbl['y'] != 0) and lbl['label']:
            # edgeLabel index - 1 = edge ID
            edge_id = lbl['idx'] - 1
            if edge_id >= 0:
                edge_id_to_label[edge_id] = lbl['label']
    
    # 遷移を状態IDにマッピングし、インデックスベースでラベルを付与
    state_transitions = []
    
    for t in transitions:
        from_state, from_dist = find_nearest_state(t['start_x'], t['start_y'])
        to_state, to_dist = find_nearest_state(t['end_x'], t['end_y'], from_state)
        
        # 距離が妥当な範囲内（200px以内）の場合のみ接続とみなす
        if from_state and to_state and from_dist < 200 and to_dist < 200:
            # edge IDに対応するラベルを取得
            label = edge_id_to_label.get(t['edge_id'], '')
            
            state_transitions.append({
                'from': from_state,
                'to': to_state,
                'label': label
            })
    
    # Mermaid stateDiagram-v2を生成
    mermaid_lines = ['stateDiagram-v2']
    
    # 状態定義（開始・終了以外）
    for sid, s in states.items():
        if s['name'] not in ('_start_', '_end_'):
            # 状態名とラベルが異なる場合はエイリアスを定義
            if s['name'] != s['label']:
                safe_label = s['label'].replace('"', "'")
                mermaid_lines.append(f'    {s["name"]}: {safe_label}')
    
    mermaid_lines.append('')
    
    # 遷移を出力
    for t in state_transitions:
        from_state = states[t['from']]
        to_state = states[t['to']]
        
        from_name = from_state['name']
        to_name = to_state['name']
        
        # 開始・終了状態は[*]に変換
        if from_name == '_start_':
            from_name = '[*]'
        if to_name == '_end_':
            to_name = '[*]'
        
        if t['label']:
            # ラベル内の特殊文字をエスケープ
            safe_label = t['label'].replace(':', '：').replace('"', "'")
            mermaid_lines.append(f'    {from_name} --> {to_name}: {safe_label}')
        else:
            mermaid_lines.append(f'    {from_name} --> {to_name}')
    
    return '\n'.join(mermaid_lines)


def extract_group_block(section, group_class):
    """指定されたグループクラスの範囲を切り出す"""
    start = section.find(f'class="{group_class}')
    if start == -1:
        return None
    
    # 終了位置を決定（次のグループまたはセクション終端）
    markers = ['class="members-group', 'class="methods-group', 
               'class="divider', 'class="label-group']
    candidates = []
    for marker in markers:
        pos = section.find(marker, start + len(group_class) + 10)
        if pos != -1:
            candidates.append(pos)
    
    end = min(candidates) if candidates else len(section)
    return section[start:end]

def extract_class_diagram(svg_content):
    """クラス図を抽出（クラス名、メンバー、メソッドを正確に抽出、属性順序非依存）"""
    
    # クラスIDを抽出（属性順序非依存）
    class_names = []
    g_tag_pattern = r'<g[^>]*>'
    for g_match in re.finditer(g_tag_pattern, svg_content):
        tag_text = g_match.group(0)
        
        # class属性にnodeが含まれるか確認
        class_match = re.search(r'class="([^"]*)"', tag_text)
        if not class_match or 'node' not in class_match.group(1):
            continue
        
        # id属性からclassId-XXX-Nを抽出
        id_match = re.search(r'id="classId-([^"]+)-(\d+)"', tag_text)
        if not id_match:
            continue
        
        class_name = id_match.group(1)
        if class_name not in class_names:
            class_names.append(class_name)
    
    if not class_names:
        return None
    
    # Mermaidクラス図を生成
    mermaid_lines = ['classDiagram']
    
    for class_name in class_names:
        # クラスのセクションを探す
        class_section = re.search(
            rf'id="classId-{re.escape(class_name)}-\d+".*?(?=id="classId-|$)',
            svg_content, re.DOTALL
        )
        
        if class_section:
            section = class_section.group(0)
            
            # 2段階アプローチ: まずグループ範囲を切り出し、その中の全<p>を抽出
            members = []
            members_block = extract_group_block(section, "members-group")
            if members_block:
                members = re.findall(r'<p[^>]*>([^<]+)</p>', members_block)
            
            methods = []
            methods_block = extract_group_block(section, "methods-group")
            if methods_block:
                methods = re.findall(r'<p[^>]*>([^<]+)</p>', methods_block)
            
            # クラス定義を出力
            mermaid_lines.append(f'    class {class_name} {{')
            for member in members:
                mermaid_lines.append(f'        {member}')
            for method in methods:
                mermaid_lines.append(f'        {method}')
            mermaid_lines.append('    }')
    
    # 関係を抽出（edgePath要素から、marker属性で種類を判定、属性順序非依存）
    # relation classを持つpath要素を探す
    relation_path_pattern = r'<path[^>]*class="[^"]*relation[^"]*"[^>]*>'
    relations = []
    
    for m in re.finditer(relation_path_pattern, svg_content):
        tag_text = m.group(0)
        
        # id属性からクラス名を抽出
        id_match = re.search(r'id="id_([^_]+)_([^_]+)_\d+"', tag_text)
        if not id_match:
            continue
        from_class = id_match.group(1)
        to_class = id_match.group(2)
        
        # marker-start属性からマーカータイプを抽出
        marker_match = re.search(r'marker-start="url\([^)]*class-([^)]+)\)"', tag_text)
        marker_type = marker_match.group(1) if marker_match else ''
        
        if from_class in class_names and to_class in class_names:
            # マーカータイプから矢印記号を決定
            if 'composition' in marker_type:
                arrow = '*--'  # コンポジション（黒ひし形）
            elif 'aggregation' in marker_type:
                arrow = 'o--'  # 集約（白ひし形）
            else:
                arrow = '-->'  # デフォルト
            relations.append((from_class, to_class, arrow))
    
    # marker-startがない場合のフォールバック（従来のパターン）
    if not relations:
        edge_pattern = r'id="id_([^_]+)_([^_]+)_\d+"'
        for m in re.finditer(edge_pattern, svg_content):
            from_class = m.group(1)
            to_class = m.group(2)
            if from_class in class_names and to_class in class_names:
                relations.append((from_class, to_class, '-->'))
    
    # 関係を出力
    if relations:
        mermaid_lines.append('')
        for from_class, to_class, arrow in relations:
            mermaid_lines.append(f'    {from_class} {arrow} {to_class}')
    
    return '\n'.join(mermaid_lines)

def main():
    """メイン処理"""
    images_dir = '/home/ubuntu/deepwiki_output/images_v5/'
    mermaid_dir = '/home/ubuntu/deepwiki_output/mermaid_v5/'
    
    os.makedirs(mermaid_dir, exist_ok=True)
    
    svg_files = [f for f in os.listdir(images_dir) if f.endswith('.svg')]
    
    stats = {'total': 0, 'success': 0, 'with_subgraph': 0, 'failed': 0}
    
    for svg_file in sorted(svg_files):
        svg_path = os.path.join(images_dir, svg_file)
        mermaid_file = svg_file.replace('.svg', '.mmd')
        mermaid_path = os.path.join(mermaid_dir, mermaid_file)
        
        stats['total'] += 1
        
        try:
            with open(svg_path, 'r', encoding='utf-8') as f:
                svg_content = f.read()
            
            mermaid = extract_mermaid_from_svg(svg_content)
            
            if mermaid and len(mermaid) > 50:
                with open(mermaid_path, 'w', encoding='utf-8') as f:
                    f.write(mermaid)
                stats['success'] += 1
                
                if 'subgraph' in mermaid:
                    stats['with_subgraph'] += 1
                    print(f"[OK+SG] {svg_file}")
                else:
                    print(f"[OK] {svg_file}")
            else:
                stats['failed'] += 1
                print(f"[SKIP] {svg_file}")
        except Exception as e:
            stats['failed'] += 1
            print(f"[ERR] {svg_file}: {e}")
    
    print(f"\n=== {_('Statistics')} ===")
    print(f"{_('Total')}: {stats['total']}")
    print(f"{_('Success')}: {stats['success']}")
    print(f"{_('With subgraph')}: {stats['with_subgraph']}")
    print(f"{_('Failed')}: {stats['failed']}")

if __name__ == '__main__':
    main()
