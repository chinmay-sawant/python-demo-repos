from typing import Optional
from fastapi import Request, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import Settings

async def get_settings(request: Request) -> Settings:
    return request.app.state.settings

async def get_session(request: Request) -> AsyncSession:
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        yield session

async def verify_tenant(
    x_tenant_id: Optional[int] = Header(None, alias="X-Tenant-Id"),
    settings: Settings = Depends(get_settings),
):
    if x_tenant_id is None:
        raise HTTPException(status_code=401, detail="X-Tenant-Id header required")
    if x_tenant_id < 1:
        raise HTTPException(status_code=401, detail="Invalid tenant")
    return x_tenant_id
