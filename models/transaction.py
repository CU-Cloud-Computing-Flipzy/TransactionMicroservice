from __future__ import annotations
from uuid import UUID, uuid4
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field, condecimal
from enum import Enum
from typing import Literal


class TransactionStatus(str, Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Transaction(BaseModel):
    """
    Transaction model for a second-hand marketplace.

    A transaction is a one-shot purchase:
    - VIRTUAL items are processed immediately (COMPLETED or FAILED at creation).
    - REAL items require asynchronous processing, starting as PENDING and 
      later transitioning to COMPLETED or FAILED.
    """

    id: UUID = Field(default_factory=uuid4, description="Transaction ID")

    buyer_id: UUID = Field(..., description="Buyer ID")
    seller_id: UUID = Field(..., description="Seller ID")
    item_id: UUID = Field(..., description="Item ID")

    order_type: Literal["REAL", "VIRTUAL"] = Field(
        ..., description="REAL item or VIRTUAL item"
    )

    title_snapshot: str = Field(..., description="Item title at purchase time")

    price_snapshot: condecimal(gt=0, max_digits=20, decimal_places=2) = Field(
        ..., description="Price at purchase time"
    )

    status: TransactionStatus = Field(
        default=TransactionStatus.PENDING,
        description="Order status"
    )

    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Creation timestamp (UTC)"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": "99999999-9999-4999-8999-999999999999",
                "buyer_id": "f5b4b9d3-abc2-4d9b-912d-0c3b9e49d1af",
                "seller_id": "123e4567-e89b-12d3-a456-426614174000",
                "item_id": "a4c8b0c2-7a27-49d7-9af7-59fe2d7e5d3f",
                "order_type": "REAL",
                "title_snapshot": "Used iPhone 12 128GB",
                "price_snapshot": "350.00",
                "status": "PENDING",
                "created_at": "2025-01-15T10:20:30Z"
            }
        }
    }
