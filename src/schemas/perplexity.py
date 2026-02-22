from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

class PerplexityRequest(BaseModel):
    query: str = Field(..., description="The query to send to Perplexity")
    context: Optional[str] = Field(None, description="Additional context or code snippet")

class PerplexityResponse(BaseModel):
    answer: str = Field(..., description="The generated answer from Perplexity")
    sources: List[str] = Field(default_factory=list, description="Citations or sources")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata like model used")
