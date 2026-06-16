from fastapi import APIRouter, Request

from src.database import list_reports

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("")
async def list_reports_api(request: Request, severity: str = None, limit: int = 50, offset: int = 0):
    rows = await list_reports(request.app.state.db, limit=limit, offset=offset, severity=severity)
    return {"reports": rows, "total": len(rows)}
