from fastapi import APIRouter, Request, HTTPException
import httpx, os

router = APIRouter(prefix="/os", tags=["os-autonomy"])
OS_AGENT_URL = os.getenv("OS_AGENT_URL", "http://192.168.0.55:7801")
OS_AGENT_KEY = os.getenv("OS_AGENT_KEY", "joao-os-2026")

@router.api_route("/{path:path}", methods=["GET","POST","PUT","DELETE"])
async def proxy_os(path: str, request: Request):
    body = await request.body()
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.request(
            method=request.method,
            url=f"{OS_AGENT_URL}/{path}",
            headers={"X-API-Key": OS_AGENT_KEY, "Content-Type": "application/json"},
            content=body
        )
    return r.json()
