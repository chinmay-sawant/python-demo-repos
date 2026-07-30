from fastapi import APIRouter, Depends
from app.dependencies import get_settings, get_session, verify_tenant
from app.schemas import IngestRequest, IngestResponse
from app.services.ingest import IngestService

router = APIRouter(prefix="/api/v1", tags=["ingest"])

@router.post("/ingest", response_model=IngestResponse)
async def ingest(
    body: IngestRequest,
    tenant_id: int = Depends(verify_tenant),
    session=Depends(get_session),
    settings=Depends(get_settings),
):
    service = IngestService(session, settings)
    return await service.process_batch(tenant_id=tenant_id, request=body)
