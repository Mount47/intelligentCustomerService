"""管理员 / 运维监测接口（ADR-12）。M8 先落 metrics（压测削峰观察 + dashboard 共用）。

后续（M11 前端对接时）补：/sessions、/sessions/{id}、/tickets、/tickets/{id}。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.observability.metrics import compute_metrics

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/metrics")
def metrics(db: Session = Depends(get_db)) -> dict:
    """运行时指标：状态分布 + 队列深度 + agent 表现 + token/成本。"""
    return compute_metrics(db)
