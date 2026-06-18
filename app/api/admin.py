"""管理员 / 运维监测接口（ADR-12，前端契约对齐）。

metrics（削峰/概览）+ tickets/sessions 列表与详情，供 Vue 运维监测端消费。
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.admin import AdminMetrics, SessionSummary, TicketDetail, TicketSummary
from app.schemas.chat import ChatSession
from app.services import admin_service, chat_service

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/metrics", response_model=AdminMetrics)
def metrics(db: Session = Depends(get_db)) -> AdminMetrics:
    return admin_service.build_admin_metrics(db)


@router.get("/tickets", response_model=List[TicketSummary])
def list_tickets(db: Session = Depends(get_db)) -> list[TicketSummary]:
    return admin_service.list_tickets(db)


@router.get("/tickets/{ticket_id}", response_model=TicketDetail)
def get_ticket(ticket_id: int, db: Session = Depends(get_db)) -> TicketDetail:
    t = admin_service.get_ticket_detail(db, ticket_id)
    if t is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    return t


@router.get("/sessions", response_model=List[SessionSummary])
def list_sessions(db: Session = Depends(get_db)) -> list[SessionSummary]:
    return admin_service.list_sessions(db)


@router.get("/sessions/{session_id}", response_model=ChatSession)
def get_session(session_id: int, db: Session = Depends(get_db)) -> ChatSession:
    view = chat_service.get_session_view(db, session_id)
    if view is None:
        raise HTTPException(status_code=404, detail="session not found")
    return view
