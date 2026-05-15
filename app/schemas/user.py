"""User schemas — settings and profile."""

from typing import Optional
from pydantic import BaseModel, Field, field_validator


class UserSettingsRequest(BaseModel):
    materials: Optional[list[dict]] = None
    pricing_config: Optional[dict] = None
    use_prusaslicer: Optional[bool] = None
    slicer_preset_id: Optional[int] = None

    @field_validator("materials")
    @classmethod
    def validate_materials(cls, v):
        if v is not None:
            if not isinstance(v, list) or len(v) > 50:
                raise ValueError("材料列表不能超过 50 项")
            for m in v:
                if not isinstance(m, dict):
                    raise ValueError("材料格式不合法")
                if "name" not in m:
                    raise ValueError("材料名称不能为空")
        return v


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1, max_length=100)
    new_password: str = Field(..., min_length=6, max_length=100)
    confirm_password: str = Field(..., min_length=6, max_length=100)

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v, info):
        if "new_password" in info.data and v != info.data["new_password"]:
            raise ValueError("两次输入的密码不一致")
        return v


class MembershipPlan(BaseModel):
    code: str
    name: str
    price_cny: float
    currency: str = "CNY"
    duration_days: int


class BillingCheckoutRequest(BaseModel):
    plan_code: str = Field(..., min_length=3, max_length=40)
    redirect_url: Optional[str] = Field(default=None, max_length=500)


class BillingOrder(BaseModel):
    order_no: str
    plan_code: str
    amount_cny: float
    currency: str
    provider: str
    status: str
    created_at: str
    paid_at: Optional[str] = None
