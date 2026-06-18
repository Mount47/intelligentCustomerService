"""知识库：关键词检索（MVP，§12）。回答政策须能引用来源；无答案则拒答/转人工。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import KnowledgeDoc

# 分类 -> 触发关键词（命中即召回对应政策）
CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "refund_policy": ("退款", "退钱", "退费"),
    "return_policy": ("退货", "寄回"),
    "logistics_policy": ("物流", "快递", "催", "签收", "没收到", "未收到"),
    "invoice_policy": ("发票", "开票", "税号", "抬头"),
    "coupon_policy": ("优惠券", "券", "满减"),
    "complaint_policy": ("投诉", "质量", "破损"),
    "handoff_policy": ("人工", "转接"),
}


def search_policy_docs(db: Session, query: str, limit: int = 3) -> list[KnowledgeDoc]:
    q = query or ""
    by_cat = {
        d.category: d
        for d in db.scalars(select(KnowledgeDoc).where(KnowledgeDoc.enabled.is_(True))).all()
    }
    hits = [
        by_cat[cat]
        for cat, kws in CATEGORY_KEYWORDS.items()
        if cat in by_cat and any(kw in q for kw in kws)
    ]
    return hits[:limit]


def get_policy_by_category(db: Session, category: str) -> KnowledgeDoc | None:
    return db.scalar(
        select(KnowledgeDoc).where(
            KnowledgeDoc.category == category, KnowledgeDoc.enabled.is_(True)
        )
    )
