from __future__ import annotations
from uuid import UUID, uuid4
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field, condecimal


class Wallet(BaseModel):
    """
    Represents a user's unified wallet.
    Each user has only one wallet.
    """
    id: UUID = Field(default_factory=uuid4, description="Wallet ID.")
    user_id: UUID = Field(..., description="User ID who owns this wallet.")
    balance: Decimal = Field(..., ge=0, max_digits=20, decimal_places=2)

    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Creation timestamp (UTC)."
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Last update timestamp (UTC)."
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "user_id": "11111111-2222-3333-4444-555555555555",
                "balance": "500.00",
                "created_at": "2025-01-15T10:20:30Z",
                "updated_at": "2025-01-15T10:20:30Z"
            }
        }
    }
