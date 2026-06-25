"""
认证相关 Pydantic schemas
"""

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100, description="用户名")
    password: str = Field(..., min_length=1, max_length=255, description="密码")


class TokenResponse(BaseModel):
    access_token: str = Field(..., description="JWT访问令牌")
    token_type: str = Field(default="bearer", description="令牌类型")
    expires_in: int = Field(..., description="过期时间（秒）")


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., description="刷新令牌")


class UserInfo(BaseModel):
    user_id: str
    username: str
    dept: str
    role_name: str

    class Config:
        from_attributes = True
