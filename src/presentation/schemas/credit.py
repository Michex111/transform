"""Credit-related API schemas."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from src.presentation.schemas.subscription import CheckoutUiMode


class TransactionType(StrEnum):
    PURCHASE = "PURCHASE"
    CONSUMPTION = "CONSUMPTION"
    REFUND = "REFUND"
    BONUS = "BONUS"
    #: An upgrade that converted the unspent plan balance into expiring
    #: carryover credits. Ledger-only: it records the conversion, not a grant.
    CARRYOVER = "CARRYOVER"


class CreditBalanceResponse(BaseModel):
    balance: int
    tier: str
    monthly_allowance: int | None = None
    monthly_remaining: int | None = None
    # First instant of the next UTC calendar month, or None when the tier has
    # no persistent monthly credits (nothing resets for those users).
    credits_reset_at: datetime | None

    # --- Wallet split -----------------------------------------------------
    # Additive and defaulted on purpose: the API and the SPA deploy
    # independently, so an older client must keep parsing this response. Each
    # field is the *current* value of one population; ``total_available`` is the
    # server-computed sum (plan + live carryover + purchased) so the client does
    # not have to know that an expired carryover contributes zero.
    plan_remaining: int | None = None
    carryover_credits: int | None = None
    carryover_expires_at: datetime | None = None
    purchased_credits: int | None = None
    #: Whether this account spends purchased credits before plan credits for
    #: *API* usage. Browser jobs always spend plan first.
    purchased_credits_first: bool | None = None
    total_available: int | None = None


class CreditTransactionResponse(BaseModel):
    id: str
    amount: int
    transaction_type: TransactionType
    reference_id: str | None = None
    description: str | None = None
    created_at: datetime


class CreditPurchaseRequest(BaseModel):
    amount: int = Field(gt=0, le=10000, description="Number of credits to purchase")
    # See ``CheckoutRequest.ui_mode``: hosted is the default so an SPA that does
    # not know about embedded checkout keeps working unchanged.
    ui_mode: CheckoutUiMode = CheckoutUiMode.HOSTED


class CreditPricingResponse(BaseModel):
    credits: int
    price_usd: float
    price_per_credit: float


class CreditPreferenceRequest(BaseModel):
    """The user-controlled credit spend order.

    Only this one field is accepted: the wallet balances themselves are never
    client-writable.
    """

    purchased_credits_first: bool


class CreditPreferenceResponse(BaseModel):
    purchased_credits_first: bool
