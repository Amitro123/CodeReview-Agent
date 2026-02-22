from fastapi import APIRouter, Depends, HTTPException
from src.schemas.perplexity import PerplexityRequest, PerplexityResponse
from src.services.factory import ServiceFactory
from src.services.base_client import BaseClient

router = APIRouter(prefix="/perplexity", tags=["perplexity"])

def get_perplexity_client():
    return ServiceFactory.get_client("perplexity")

@router.post("/analyze", response_model=PerplexityResponse)
async def analyze_code(
    request: PerplexityRequest,
    client: BaseClient = Depends(get_perplexity_client)
):
    """
    Analyze code or answer questions using Perplexity API.
    """
    try:
        response = await client.analyze(request)
        return response
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal Server Error")
