from __future__ import annotations
from typing import List
from uuid import UUID, uuid4
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field, condecimal, conint
from enum import Enum


class TransactionStatus(str, Enum):
    PENDING = "PENDING"
    PAID = "PAID"
    CANCELLED = "CANCELLED"
    FULFILLED = "FULFILLED"
    REFUNDED = "REFUNDED"


class TransactionItem(BaseModel):
    product_id: UUID = Field(..., description="Product ID.")
    title_snapshot: str = Field(..., description="Product title at purchase time.")
    unit_price: condecimal(gt=0, max_digits=20, decimal_places=8) = Field(
        ..., description="Unit price at purchase time."
    )
    quantity: conint(gt=0) = Field(..., description="Quantity purchased.")


class Transaction(BaseModel):
    """
    Represents an order between a buyer and a seller.
    Payment is always in USD. No distinction between product types.
    """
    id: UUID = Field(default_factory=uuid4, description="Transaction ID.")
    buyer_id: UUID = Field(..., description="Buyer ID.")
    seller_id: UUID = Field(..., description="Seller ID.")
    status: TransactionStatus = Field(default=TransactionStatus.PENDING, description="Transaction status.")
    items: List[TransactionItem] = Field(default_factory=list, description="List of purchased items.")

    subtotal: condecimal(gt=0, max_digits=20, decimal_places=8) = Field(..., description="Subtotal amount.")
    total: condecimal(gt=0, max_digits=20, decimal_places=8) = Field(..., description="Total amount.")

    currency: str = Field(default="USD", description="Payment currency (always USD).")

    created_at: datetime = Field(default_factory=datetime.utcnow, description="Creation timestamp (UTC).")
    updated_at: datetime = Field(default_factory=datetime.utcnow, description="Last update timestamp (UTC).")

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": "99999999-9999-4999-8999-999999999999",
                "buyer_id": "f5b4b9d3-abc2-4d9b-912d-0c3b9e49d1af",
                "seller_id": "123e4567-e89b-12d3-a456-426614174000",
                "status": "PENDING",
                "items": [
                    {
                        "product_id": "a4c8b0c2-7a27-49d7-9af7-59fe2d7e5d3f",
                        "title_snapshot": "Hydrating Mask 5-pack",
                        "unit_price": "19.99000000",
                        "quantity": 2
                    }
                ],
                "subtotal": "39.98000000",
                "total": "39.98000000",
                "currency": "USD",
                "created_at": "2025-01-15T10:20:30Z",
                "updated_at": "2025-01-15T10:20:30Z"
            }
        }
    }
