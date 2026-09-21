"""Credit-related API schemas."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class TransactionType(StrEnum):
    PURCHASE = "PURCHASE"
    CONSUMPTION = "CONSUMPTION"
    REFUND = "REFUND"
    BONUS = "BONUS"


class CreditBalanceResponse(BaseModel):
    balance: int
    tier: str
    monthly_allowance: int | None = None
    monthly_remaining: int | None = None
    # First instant of the next UTC calendar month, or None when the tier has
    # no persistent monthly credits (nothing resets for those users).
    credits_reset_at: datetime | None


class CreditTransactionResponse(BaseModel):
    id: str
    amount: int
    transaction_type: TransactionType
    reference_id: str | None = None
    description: str | None = None
    created_at: datetime


class CreditPurchaseRequest(BaseModel):
    amount: int = Field(gt=0, le=10000, description="Number of credits to purchase")


class CreditPricingResponse(BaseModel):
    credits: int
    price_usd: float
    price_per_credit: float
