"""认证 API 契约。"""
from app.schemas.common import CamelModel


class TokenRequest(CamelModel):
    username: str
    password: str


class TokenResponse(CamelModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user_id: int
    role: str
