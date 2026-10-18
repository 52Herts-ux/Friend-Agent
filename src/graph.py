"""记忆关系图谱（类似 Obsidian Graph View）。

用 ChromaDB 里每条记忆的 embedding 计算两两余弦相似度，
相似度高于阈值的记忆之间建立一条「关系边」，渲染成 HTML 力导向图。

节点按 category 着色，hover 显示完整记忆文本，可拖拽/缩放。
"""
from __future__ import annotations

import html
from typing import Dict, List, Tuple

# category → 颜色（hex）
_CATEGORY_COLORS: Dict[str, str] = {
    "user_preference": "#e8a87c",   # 偏好（橙）
    "plan": "#7fb3d5",              # 计划（蓝）
    "fact": "#82c8a0",              # 事实（绿）
    "note": "#b0b0b0",              # 笔记（灰）
    "reflection": "#c39bd3",        # 反思（紫）
    "agent_thought": "#f0a3c0",     # 想法（粉）
    "daily_summary": "#7fd4d4",     # 总结（青）
}
_DEFAULT_COLOR = "#b0b0b0"

# category → 中文标签
_CATEGORY_LABELS: Dict[str, str] = {
    "user_preference": "偏好", "plan": "计划", "fact": "事实", "note": "笔记",
    "reflection": "反思", "agent_thought": "想法", "daily_summary": "总结",
}


def build_graph(memory, threshold: float = 0.55,
                max_edges: int = 100) -> Tuple[List[Dict], List[Dict]]:
    """返回 (nodes, edges)，节点为记忆、边为语义相似关系。"""
    import numpy as np
    data = memory.collection.get(include=["embeddings", "documents", "metadatas"])
    docs = data.get("documents") or []
    metas = data.get("metadatas") or []
    embs = data.get("embeddings")

    nodes: List[Dict] = []
    for i, (doc, meta) in enumerate(zip(docs, metas)):
        cat = (meta or {}).get("category", "note")
        text = doc or ""
        nodes.append({
            "id": i,
            "label": text[:16] + ("…" if len(text) > 16 else ""),
            "title": text,   # hover 显示完整文本
            "category": cat,
            "color": _CATEGORY_COLORS.get(cat, _DEFAULT_COLOR),
        })

    edges: List[Dict] = []
    if embs is not None and len(embs) > 1:
        try:
            arr = np.array(embs, dtype=np.float32)
            norms = arr / (np.linalg.norm(arr, axis=1, keepdims=True) + 1e-8)
            sim = norms @ norms.T
            n = len(arr)
            for i in range(n):
                for j in range(i + 1, n):
                    s = float(sim[i][j])
                    if s > threshold:
                        edges.append({"from": i, "to": j, "value": round(s, 3)})
            # 只保留最相似的 top max_edges
            edges.sort(key=lambda e: -e["value"])
            edges = edges[:max_edges]
        except Exception:
            edges = []
    return nodes, edges


def render_html(nodes: List[Dict], edges: List[Dict]) -> str:
    """渲染成 vis-network 力导向图的 HTML 字符串。"""
    nodes_json = _to_json(nodes)
    edges_json = _to_json(edges)
    legend = "".join(
        f'<span class="legend-item"><span class="dot" style="background:{color}"></span>'
        f'{label}</span>'
        for color, label in [
            (_CATEGORY_COLORS.get(k, _DEFAULT_COLOR), _CATEGORY_LABELS.get(k, k))
            for k in _CATEGORY_COLORS
        ]
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Friend Agent 记忆图谱</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
  body {{ margin: 0; background: #18181e; color: #d4d4d4; font-family: sans-serif; }}
  #legend {{ position: absolute; top: 12px; left: 12px; z-index: 10;
            display: flex; gap: 12px; flex-wrap: wrap; font-size: 13px; }}
  .legend-item {{ display: flex; align-items: center; gap: 5px; }}
  .dot {{ width: 12px; height: 12px; border-radius: 50%; display: inline-block; }}
  #network {{ position: absolute; top: 0; left: 0; width: 100%; height: 100%; }}
  #info {{ position: absolute; bottom: 10px; right: 14px; z-index: 10;
          color: #666; font-size: 12px; }}
</style>
</head>
<body>
<div id="legend">{legend}</div>
<div id="network"></div>
<div id="info">拖拽节点 · 滚轮缩放 · hover 看完整内容</div>
<script>
  const nodes = new vis.DataSet({nodes_json});
  const edges = new vis.DataSet({edges_json});
  const container = document.getElementById('network');
  const data = {{ nodes, edges }};
  const options = {{
    nodes: {{
      shape: 'dot', size: 14,
      font: {{ color: '#d4d4d4', size: 13 }},
      borderWidth: 0,
    }},
    edges: {{
      color: {{ opacity: 0.35 }},
      smooth: {{ enabled: true, type: 'continuous' }},
    }},
    physics: {{
      stabilization: {{ iterations: 200 }},
      barnesHut: {{ gravitationalConstant: -2500, springLength: 140, springConstant: 0.04 }},
    }},
    interaction: {{ hover: true, tooltipDelay: 100 }},
  }};
  new vis.Network(container, data, options);
</script>
</body>
</html>"""


def _to_json(obj) -> str:
    """把 Python 结构转成 JSON（转义中文安全）。"""
    import json
    return json.dumps(obj, ensure_ascii=False)


def write_graph(memory, output_path: str = "data/memory_graph.html",
                threshold: float = 0.55) -> Tuple[int, int]:
    """生成关系图谱 HTML 文件，返回 (节点数, 边数)。"""
    nodes, edges = build_graph(memory, threshold=threshold)
    html_text = render_html(nodes, edges)
    from pathlib import Path
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html_text, encoding="utf-8")
    return len(nodes), len(edges)
