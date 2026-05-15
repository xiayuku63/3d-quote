"""Quote schemas — quote requests and results."""

from typing import Optional
from pydantic import BaseModel, Field


class QuoteFileResult(BaseModel):
    """Single file quote result."""
    filename: str
    status: str  # "success" or "failed"
    error: Optional[str] = None
    volume_cm3: float = 0.0
    surface_area_cm2: float = 0.0
    surface_area_to_volume_ratio: float = 0.0
    difficulty_score: float = 0.0
    difficulty_multiplier: float = 1.0
    difficulty_markup_percent: float = 0.0
    dimensions: str = ""
    weight_g: float = 0.0
    estimated_time_h: float = 0.0
    unit_time_h: float = 0.0
    cost_cny: float = 0.0
    cost_cny_original: float = 0.0
    unit_cost_cny: float = 0.0
    quantity: int = 1
    color: str = "White"
    material: str = "PLA"
    layer_height: float = 0.2
    infill: int = 20
    effective_weight_g: float = 0.0
    cost_breakdown: Optional[dict] = None


class QuoteResponse(BaseModel):
    """Multi-file quote response."""
    total_files: int
    success_count: int
    failed_count: int
    summary_total_cost_cny: float
    summary_total_weight_g: float
    summary_total_time_h: float
    results: list[QuoteFileResult]
    membership_level: str = "free"
    membership_expires_at: Optional[int] = None
    member_discount_percent: float = 0.0


class FormulaValidateRequest(BaseModel):
    unit_cost_formula: str = Field(..., min_length=1, max_length=800)
    total_cost_formula: str = Field(..., min_length=1, max_length=800)


class QuoteHistoryItem(BaseModel):
    id: int
    filename: str
    material: str
    color: Optional[str] = None
    quantity: int
    volume_cm3: float
    weight_g: float
    estimated_time_h: float
    cost_cny: float
    dimensions: Optional[str] = None
    status: str
    error_msg: Optional[str] = None
    created_at: str
