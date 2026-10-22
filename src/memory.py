"""立体记忆（ChromaDB 向量库，借鉴 Pi `pi-durable` 的持久化 + Hermes 的持久记忆）。

本地 BGE-M3 embedding（零成本），metadata：timestamp / category / importance。
"""
from __future__ import annotations

import time
import uuid
from typing import Dict, List, Optional

import logging

import chromadb
from chromadb.utils import embedding_functions

logger = logging.getLogger("friend-agent")

# 用户本人说的话（category 白名单）：总结/归纳时只取这些，区分「用户说」vs「Agent 说」
USER_CATEGORIES = ("user_preference", "plan", "fact", "note")


def _normalize(text: str) -> str:
    """归一化文本用于去重（去空格/标点/大小写）。"""
    import re
    return re.sub(r"[\s，。！？、,.!?；;：:\"'「」『』（）()\-_—·…]", "",
                  (text or "").lower())


def _make_embedding(embed_model: str, ollama_url: str):
    """优先 Ollama 本地 bge-m3（零成本、中文好）；不可用则降级默认嵌入。"""
    # 1. 尝试 Ollama 本地嵌入
    try:
        import requests
        resp = requests.get(f"{ollama_url}/api/tags", timeout=3)
        models = [m["name"] for m in resp.json().get("models", [])]
        if any(embed_model in m or m.endswith(embed_model) or embed_model.split(":")[0] in m
               for m in models):
            logger.info("使用 Ollama 本地嵌入模型: %s", embed_model)
            return embedding_functions.OllamaEmbeddingFunction(
                url=f"{ollama_url}/api/embeddings", model_name=embed_model)
        logger.warning("Ollama 缺少 %s，降级使用 ChromaDB 默认嵌入", embed_model)
    except Exception as exc:
        logger.warning("Ollama 不可用（%s），降级使用 ChromaDB 默认嵌入", exc)
    # 2. 降级：ChromaDB 默认（all-MiniLM-L6-v2，首次使用会下载模型）
    return embedding_functions.DefaultEmbeddingFunction()


class MemoryStore:
    """本地向量记忆库，支持增删查。"""

    def __init__(self, persist_dir: str, embed_model: str = "bge-m3",
                 ollama_url: str = "http://localhost:11434"):
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._ef = _make_embedding(embed_model, ollama_url)
        # 先 get（复用已持久化的 embedding），不存在才 create，避免 embedding 切换冲突
        try:
            self.collection = self._client.get_collection(name="agent_memory")
        except Exception:
            self.collection = self._client.create_collection(
                name="agent_memory", embedding_function=self._ef,
                metadata={"hnsw:space": "cosine"},
            )
        # 已存文本缓存（归一化后），用于 store 去重
        self._texts: set = set()
        try:
            for d in (self.collection.get().get("documents") or []):
                if d:
                    self._texts.add(_normalize(d))
        except Exception:
            pass

    def store(self, text: str, category: str = "note", importance: int = 1,
              metadata: Optional[Dict] = None, dedup: bool = True) -> str:
        """写入一条记忆，返回 id。dedup=True 时，完全相同的文本不再重复存储。"""
        text = (text or "").strip()
        if not text:
            return ""
        if dedup:
            key = _normalize(text)
            if key in self._texts:
                logger.info("跳过重复记忆: %s", text[:30])
                return ""
            self._texts.add(key)
        meta = {"timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "category": category, "importance": importance}
        if metadata:
            meta.update(metadata)
        mem_id = f"mem_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        self.collection.add(ids=[mem_id], documents=[text], metadatas=[meta])
        return mem_id

    def query(self, query_text: str, n_results: int = 5,
              category=None) -> List[Dict]:
        """按语义检索相关记忆，返回 [{text, metadata, distance}, ...]。

        category 可为单个字符串，或字符串列表（列表用 $in 匹配）。
        """
        if category:
            if isinstance(category, (list, tuple, set)):
                where = {"category": {"$in": list(category)}}
            else:
                where = {"category": category}
        else:
            where = None
        res = self.collection.query(
            query_texts=[query_text], n_results=n_results, where=where,
        )
        out = []
        for i in range(len(res["documents"][0])):
            doc = res["documents"][0][i]
            if doc is None:
                continue
            out.append({
                "text": doc,
                "metadata": res["metadatas"][0][i] if res.get("metadatas") else {},
                "distance": res["distances"][0][i] if res.get("distances") else None,
            })
        return out

    def recall_as_context(self, query_text: str, n_results: int = 3) -> str:
        """把检索到的记忆拼成一段可注入 System Prompt 的文本。"""
        mems = self.query(query_text, n_results=n_results)
        if not mems:
            return ""
        lines = [f"- {m['text']}" for m in mems]
        return "【相关记忆】\n" + "\n".join(lines)

    def count(self) -> int:
        return self.collection.count()

    def clear(self) -> int:
        """清空所有记忆，返回删除的条数。"""
        try:
            before = self.collection.count()
            all_ids = self.collection.get()["ids"]
            if all_ids:
                self.collection.delete(ids=all_ids)
            return before
        except Exception as exc:
            logger.warning("清空记忆失败: %s", exc)
            return 0

    def delete_by_category(self, category: str) -> int:
        """删除指定 category 的所有记忆，返回删除条数（用于清理 Agent 说的污染数据）。"""
        try:
            data = self.collection.get(where={"category": category})
            ids = data.get("ids") or []
            if ids:
                self.collection.delete(ids=ids)
            return len(ids)
        except Exception as exc:
            logger.warning("按类别删除失败: %s", exc)
            return 0

    def all(self) -> List[Dict]:
        """返回所有记忆 [{text, metadata}, ...]，用于记忆图谱。"""
        try:
            data = self.collection.get()
        except Exception as exc:
            logger.warning("获取记忆失败: %s", exc)
            return []
        out: List[Dict] = []
        docs = data.get("documents") or []
        metas = data.get("metadatas") or []
        for i in range(len(docs)):
            out.append({
                "text": docs[i],
                "metadata": metas[i] if i < len(metas) else {},
            })
        return out
