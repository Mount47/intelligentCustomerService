"""Knowledge 工具（只读）。"""
from __future__ import annotations

from app.services import knowledge_service
from app.tools.base import RegisteredTool, ToolContext, err, ok


def search_policy_docs(ctx: ToolContext, query: str):
    docs = knowledge_service.search_policy_docs(ctx.db, query)
    return ok({"docs": [
        {"title": d.title, "category": d.category, "version": d.version, "content": d.content}
        for d in docs
    ]})


def get_policy_by_category(ctx: ToolContext, category: str):
    d = knowledge_service.get_policy_by_category(ctx.db, category)
    if not d:
        return err("policy_not_found", "未找到对应政策")
    return ok({"title": d.title, "category": d.category, "version": d.version, "content": d.content})


TOOLS = [
    RegisteredTool("search_policy_docs", "按问题关键词检索售后政策（引用来源用）。",
                   {"type": "object", "properties": {"query": {"type": "string"}},
                    "required": ["query"]}, search_policy_docs),
    RegisteredTool("get_policy_by_category", "按分类取政策文档。",
                   {"type": "object", "properties": {"category": {"type": "string"}},
                    "required": ["category"]}, get_policy_by_category),
]
