"""Pydantic request/response models."""

from typing import Optional, List
from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=100)
    register_channel: str = Field(..., min_length=4, max_length=10)
    email: Optional[str] = Field(default=None, min_length=3, max_length=254)
    phone: Optional[str] = Field(default=None, min_length=7, max_length=20)
    email_code: Optional[str] = Field(default=None, min_length=4, max_length=10)
    phone_code: Optional[str] = Field(default=None, min_length=4, max_length=10)
    captcha_id: str = Field(..., min_length=8, max_length=80)
    captcha_code: str = Field(..., min_length=4, max_length=10)
    accept_terms: bool
    accept_privacy: bool


class LoginRequest(BaseModel):
    identifier: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=6, max_length=100)
    captcha_id: str = Field(..., min_length=8, max_length=80)
    captcha_code: str = Field(..., min_length=4, max_length=10)
    accept_terms: bool
    accept_privacy: bool


class VerifySendRequest(BaseModel):
    channel: str = Field(..., min_length=4, max_length=10)
    target: str = Field(..., min_length=3, max_length=254)


class VerifyConfirmRequest(BaseModel):
    channel: str = Field(..., min_length=4, max_length=10)
    target: str = Field(..., min_length=3, max_length=254)
    code: str = Field(..., min_length=4, max_length=10)


class RegisterCheckRequest(BaseModel):
    field: str = Field(..., min_length=5, max_length=20)
    value: str = Field(..., min_length=1, max_length=254)


class MaterialItem(BaseModel):
    name: str
    density: float
    price_per_kg: float
    colors: List[str] = []


class PricingConfig(BaseModel):
    machine_hourly_rate_cny: float = 15.0
    setup_fee_cny: float = 0.0
    min_job_fee_cny: float = 0.0
    material_waste_percent: float = 5.0
    support_percent_of_model: float = 0.0
    post_process_fee_per_part_cny: float = 0.0
    difficulty_coefficient: float = 0.25
    difficulty_ratio_low: float = 0.8
    difficulty_ratio_high: float = 4.0
    unit_cost_formula: str = ""
    total_cost_formula: str = ""
    use_bambu: Optional[int] = 0
    use_prusaslicer: Optional[int] = 0
    prusa_time_correction: Optional[float] = 0.44
    bambu_support_mode: Optional[str] = "diff"
    support_price_per_g: Optional[float] = 0.0
    time_overhead_min: Optional[float] = 5.0
    time_vol_min_per_cm3: Optional[float] = 0.8
    time_area_min_per_cm2: Optional[float] = 0.0
    time_ref_layer_height_mm: Optional[float] = 0.2
    time_layer_height_exponent: Optional[float] = 1.0
    time_ref_infill_percent: Optional[float] = 20.0
    time_infill_coefficient: Optional[float] = 1.0


class UserSettingsUpdate(BaseModel):
    materials: Optional[List[MaterialItem]] = None
    colors: Optional[List[str]] = None
    pricing_config: Optional[PricingConfig] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    email_code: Optional[str] = None
    phone_code: Optional[str] = None
    accept_terms: Optional[bool] = None
    accept_privacy: Optional[bool] = None


class SlicerPresetGenerateRequest(BaseModel):
    slicer: str = Field(..., min_length=4, max_length=20)
    layer_height: float = Field(0.2, ge=0.05, le=1.0)
    infill_percent: int = Field(20, ge=0, le=100)
    wall_count: int = Field(3, ge=1, le=20)
    print_ini: str = Field(..., min_length=1, max_length=100)
    filament_name: str = Field(..., min_length=1, max_length=40)
    temperature: int = Field(220, ge=180, le=300)
    bed_temperature: int = Field(60, ge=0, le=120)


class AdminMembershipUpdateRequest(BaseModel):
    membership_level: str = Field(..., min_length=3, max_length=30)
    membership_expires_at: Optional[str] = None


class BillingCheckoutRequest(BaseModel):
    plan_code: str = Field(..., min_length=3, max_length=40)
    redirect_url: Optional[str] = Field(default=None, max_length=500)


class BillingMockCompleteRequest(BaseModel):
    order_no: str = Field(..., min_length=10, max_length=80)


class FormulaValidateRequest(BaseModel):
    unit_cost_formula: str = Field(..., min_length=1, max_length=800)
    total_cost_formula: str = Field(..., min_length=1, max_length=800)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1, max_length=100)
    new_password: str = Field(..., min_length=6, max_length=100)
    captcha_id: str = Field(..., min_length=8, max_length=80)
    captcha_code: str = Field(..., min_length=4, max_length=10)
