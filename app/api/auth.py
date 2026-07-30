"""登录换取短期 Bearer Token。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import issue_access_token, verify_password
from app.db.models import User
from app.db.session import get_db
from app.schemas.auth import TokenRequest, TokenResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/token", response_model=TokenResponse)
def token(payload: TokenRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.scalar(select(User).where(User.username == payload.username))
    if user is None or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid username or password")
    ttl = get_settings().auth_token_ttl_seconds
    return TokenResponse(
        access_token=issue_access_token(user.id, ttl_seconds=ttl),
        expires_in=ttl,
        user_id=user.id,
        role=user.role,
    )
