from __future__ import annotations

import json
import time
import threading
import hashlib
from datetime import datetime
from decimal import Decimal
from typing import Dict, Optional, Any
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from models.wallet import Wallet
from models.cart import CartItem
from models.transaction import (
    Transaction,
    TransactionStatus,
)


# ==========================================================
# In-memory data store
# ==========================================================
wallets: Dict[UUID, Wallet] = {}
cart_items: Dict[UUID, CartItem] = {}
transactions: Dict[UUID, Transaction] = {}
operations: Dict[UUID, Dict[str, Any]] = {}

db_lock = threading.Lock()


# ==========================================================
# Request Models
# ==========================================================
class WalletCreate(BaseModel):
    user_id: UUID


class TransactionCreate(BaseModel):
    buyer_id: UUID
    seller_id: UUID
    total: Decimal = Field(..., gt=0, description="Must be > 0")


class DepositRequest(BaseModel):
    amount: Decimal = Field(..., gt=0)


# ==========================================================
# ETag helper
# ==========================================================
def compute_etag(obj) -> str:
    """Compute SHA-256 hash of serialized model."""
    payload = obj.model_dump(mode="json")
    json_str = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(json_str.encode()).hexdigest()


# ==========================================================
# HATEOAS link builders
# ==========================================================
def wallet_links(w: Wallet):
    return {
        "self": f"/wallets/{w.id}",
        "owner_wallets": f"/wallets?user_id={w.user_id}",
        "owner_transactions": f"/transactions?buyer_id={w.user_id}",
    }


def cart_item_links(c: CartItem):
    return {
        "self": f"/cart-items/{c.id}",
        "owner_cart": f"/cart-items?user_id={c.user_id}",
    }


def transaction_links(t: Transaction):
    return {
        "self": f"/transactions/{t.id}",
        "buyer_wallets": f"/wallets?user_id={t.buyer_id}",
        "seller_wallets": f"/wallets?user_id={t.seller_id}",
        "pay": f"/transactions/{t.id}/pay",
        "cancel": f"/transactions/{t.id}/cancel",
        "fulfill": f"/transactions/{t.id}/fulfill",
        "refund": f"/transactions/{t.id}/refund",
    }


# ==========================================================
# FastAPI app
# ==========================================================
app = FastAPI(
    title="Wallet / Cart / Transaction Microservice",
    description="USD-only wallet, cart, transactions with async payments.",
    version="3.1.0",
)


# ==========================================================
# Wallet - Create
# ==========================================================
@app.post("/wallets", status_code=201)
def create_wallet(req: WalletCreate, response: Response):
    """
    Create a new wallet for a user. One wallet per user.
    """
    with db_lock:
        if any(w.user_id == req.user_id for w in wallets.values()):
            raise HTTPException(400, "User already has a wallet.")

        now = datetime.utcnow()

        wallet = Wallet(
            id=uuid4(),
            user_id=req.user_id,
            usd_balance=Decimal("0"),
            created_at=now,
            updated_at=now,
        )

        wallets[wallet.id] = wallet

    response.headers["Location"] = f"/wallets/{wallet.id}"
    response.headers["ETag"] = compute_etag(wallet)

    data = wallet.model_dump()
    data["_links"] = wallet_links(wallet)
    return data


# ==========================================================
# Wallet - List / Get
# ==========================================================
@app.get("/wallets")
def list_wallets(user_id: Optional[UUID] = Query(None)):
    """List all wallets, optionally filter by user."""
    with db_lock:
        result = list(wallets.values())
        if user_id:
            result = [w for w in result if w.user_id == user_id]

    return [{**w.model_dump(), "_links": wallet_links(w)} for w in result]


@app.get("/wallets/{wallet_id}")
def get_wallet(wallet_id: UUID, request: Request, response: Response):
    """Get wallet by ID with ETag support."""
    with db_lock:
        wallet = wallets.get(wallet_id)
        if not wallet:
            raise HTTPException(404, "Wallet not found.")

    etag = compute_etag(wallet)
    response.headers["ETag"] = etag

    if request.headers.get("If-None-Match") == etag:
        response.status_code = 304
        return

    data = wallet.model_dump()
    data["_links"] = wallet_links(wallet)
    return data


# ==========================================================
# Wallet - Deposit
# ==========================================================
@app.post("/wallets/{wallet_id}/deposit")
def deposit(wallet_id: UUID, req: DepositRequest):
    """Deposit USD into wallet."""
    with db_lock:
        wallet = wallets.get(wallet_id)
        if not wallet:
            raise HTTPException(404, "Wallet not found.")

        wallet.usd_balance += req.amount
        wallet.updated_at = datetime.utcnow()

        data = wallet.model_dump()
        data["_links"] = wallet_links(wallet)
        return data


# ==========================================================
# Cart Endpoints
# ==========================================================
@app.post("/cart-items", status_code=201)
def create_cart_item(payload: CartItem, response: Response):
    """
    Create a cart item. ID and timestamp generated server-side.
    """
    item = CartItem(
        id=uuid4(),
        added_at=datetime.utcnow(),
        **payload.model_dump(exclude={"id", "added_at"}),
    )

    with db_lock:
        cart_items[item.id] = item

    response.headers["Location"] = f"/cart-items/{item.id}"
    data = item.model_dump()
    data["_links"] = cart_item_links(item)
    return data


@app.get("/cart-items")
def list_cart_items(user_id: Optional[UUID] = None):
    """List cart items, optionally filter by user."""
    with db_lock:
        items = list(cart_items.values())
        if user_id:
            items = [c for c in items if c.user_id == user_id]

    return [{**c.model_dump(), "_links": cart_item_links(c)} for c in items]


@app.get("/cart-items/{cart_item_id}")
def get_cart_item(cart_item_id: UUID):
    """Get a cart item."""
    with db_lock:
        item = cart_items.get(cart_item_id)
        if not item:
            raise HTTPException(404, "Cart item not found.")

    data = item.model_dump()
    data["_links"] = cart_item_links(item)
    return data


# ==========================================================
# Transaction - Create
# ==========================================================
@app.post("/transactions", status_code=201)
def create_transaction(req: TransactionCreate, response: Response):
    """
    Create a transaction (USD-only).
    Ensures buyer != seller.
    """
    if req.buyer_id == req.seller_id:
        raise HTTPException(400, "Buyer and seller cannot be the same user.")

    now = datetime.utcnow()

    tx = Transaction(
        id=uuid4(),
        buyer_id=req.buyer_id,
        seller_id=req.seller_id,
        total=req.total,
        status=TransactionStatus.PENDING,
        created_at=now,
        updated_at=now,
    )

    with db_lock:
        transactions[tx.id] = tx

    response.headers["Location"] = f"/transactions/{tx.id}"

    data = tx.model_dump()
    data["_links"] = transaction_links(tx)
    return data


# ==========================================================
# Transaction - List / Get
# ==========================================================
@app.get("/transactions")
def list_transactions(
    buyer_id: Optional[UUID] = None,
    seller_id: Optional[UUID] = None,
    status: Optional[TransactionStatus] = None,
):
    """List or filter transactions."""
    with db_lock:
        result = list(transactions.values())
        if buyer_id:
            result = [t for t in result if t.buyer_id == buyer_id]
        if seller_id:
            result = [t for t in result if t.seller_id == seller_id]
        if status:
            result = [t for t in result if t.status == status]

    return [{**t.model_dump(), "_links": transaction_links(t)} for t in result]


@app.get("/transactions/{transaction_id}")
def get_transaction(transaction_id: UUID):
    """Get transaction by ID."""
    with db_lock:
        tx = transactions.get(transaction_id)
        if not tx:
            raise HTTPException(404, "Transaction not found.")

    data = tx.model_dump()
    data["_links"] = transaction_links(tx)
    return data


# ==========================================================
# Async Payment Processor
# ==========================================================
def async_pay(operation_id: UUID, transaction_id: UUID):
    """Simulate asynchronous payment processing."""
    with db_lock:
        op = operations[operation_id]
        op["status"] = "RUNNING"
        op["updated_at"] = datetime.utcnow()

    time.sleep(2)

    with db_lock:
        op = operations[operation_id]
        tx = transactions.get(transaction_id)

        # Transaction missing
        if not tx:
            op["status"] = "FAILED"
            op["error"] = "Transaction not found."
            op["updated_at"] = datetime.utcnow()
            return

        # Wrong state
        if tx.status != TransactionStatus.PENDING:
            op["status"] = "FAILED"
            op["error"] = f"Cannot pay transaction in status: {tx.status}"
            op["updated_at"] = datetime.utcnow()
            return

        # Wallet checks
        buyer_wallet = next((w for w in wallets.values() if w.user_id == tx.buyer_id), None)
        seller_wallet = next((w for w in wallets.values() if w.user_id == tx.seller_id), None)

        if not buyer_wallet or not seller_wallet:
            op["status"] = "FAILED"
            op["error"] = "Buyer or seller wallet not found."
            op["updated_at"] = datetime.utcnow()
            return

        # Balance check
        if buyer_wallet.usd_balance < tx.total:
            op["status"] = "FAILED"
            op["error"] = "Insufficient USD balance."
            op["updated_at"] = datetime.utcnow()
            return

        # Payment
        buyer_wallet.usd_balance -= tx.total
        seller_wallet.usd_balance += tx.total
        buyer_wallet.updated_at = datetime.utcnow()
        seller_wallet.updated_at = datetime.utcnow()

        tx.status = TransactionStatus.PAID
        tx.updated_at = datetime.utcnow()

        op["status"] = "COMPLETED"
        op["updated_at"] = datetime.utcnow()


# ==========================================================
# Trigger Async Payment
# ==========================================================
@app.post("/transactions/{transaction_id}/pay", status_code=202)
def pay_transaction(transaction_id: UUID, response: Response):
    """Start asynchronous payment for a PENDING transaction."""
    with db_lock:
        tx = transactions.get(transaction_id)
        if not tx:
            raise HTTPException(404, "Transaction not found.")
        if tx.status != TransactionStatus.PENDING:
            raise HTTPException(400, "Only PENDING transactions can be paid.")

        op_id = uuid4()
        now = datetime.utcnow()

        operations[op_id] = {
            "id": op_id,
            "transaction_id": transaction_id,
            "status": "PENDING",
            "created_at": now,
            "updated_at": now,
        }

    threading.Thread(target=async_pay, args=(op_id, transaction_id), daemon=True).start()

    response.headers["Location"] = f"/operations/{op_id}"
    return {
        "id": op_id,
        "transaction_id": transaction_id,
        "status": "PENDING",
        "created_at": now,
        "updated_at": now,
        "_links": {"self": f"/operations/{op_id}", "transaction": f"/transactions/{transaction_id}"},
    }


# ==========================================================
# Transaction - Cancel
# ==========================================================
@app.post("/transactions/{transaction_id}/cancel")
def cancel_transaction(transaction_id: UUID):
    """Cancel a PENDING transaction."""
    with db_lock:
        tx = transactions.get(transaction_id)
        if not tx:
            raise HTTPException(404, "Transaction not found.")
        if tx.status != TransactionStatus.PENDING:
            raise HTTPException(400, "Only PENDING transactions can be cancelled.")

        tx.status = TransactionStatus.CANCELLED
        tx.updated_at = datetime.utcnow()

    data = tx.model_dump()
    data["_links"] = transaction_links(tx)
    return data


# ==========================================================
# Transaction - Fulfill
# ==========================================================
@app.post("/transactions/{transaction_id}/fulfill")
def fulfill_transaction(transaction_id: UUID):
    """Fulfill a PAID transaction."""
    with db_lock:
        tx = transactions.get(transaction_id)
        if not tx:
            raise HTTPException(404, "Transaction not found.")
        if tx.status != TransactionStatus.PAID:
            raise HTTPException(400, "Only PAID transactions can be fulfilled.")

        tx.status = TransactionStatus.FULFILLED
        tx.updated_at = datetime.utcnow()

    data = tx.model_dump()
    data["_links"] = transaction_links(tx)
    return data


# ==========================================================
# Transaction - Refund
# ==========================================================
@app.post("/transactions/{transaction_id}/refund")
def refund_transaction(transaction_id: UUID):
    """Refund a PAID or FULFILLED transaction."""
    with db_lock:
        tx = transactions.get(transaction_id)
        if not tx:
            raise HTTPException(404, "Transaction not found.")
        if tx.status not in {TransactionStatus.PAID, TransactionStatus.FULFILLED}:
            raise HTTPException(400, "Only PAID or FULFILLED transactions can be refunded.")

        buyer_wallet = next((w for w in wallets.values() if w.user_id == tx.buyer_id), None)
        seller_wallet = next((w for w in wallets.values() if w.user_id == tx.seller_id), None)

        if not buyer_wallet or not seller_wallet:
            raise HTTPException(400, "Buyer or seller wallet not found.")

        if seller_wallet.usd_balance < tx.total:
            raise HTTPException(400, "Seller has insufficient balance.")

        # Refund movement
        seller_wallet.usd_balance -= tx.total
        buyer_wallet.usd_balance += tx.total
        seller_wallet.updated_at = datetime.utcnow()
        buyer_wallet.updated_at = datetime.utcnow()

        tx.status = TransactionStatus.REFUNDED
        tx.updated_at = datetime.utcnow()

    data = tx.model_dump()
    data["_links"] = transaction_links(tx)
    return data


# ==========================================================
# Operation Status
# ==========================================================
@app.get("/operations/{operation_id}")
def get_operation(operation_id: UUID):
    """Get async operation status."""
    with db_lock:
        op = operations.get(operation_id)
        if not op:
            raise HTTPException(404, "Operation not found.")

    data = dict(op)
    data["_links"] = {
        "self": f"/operations/{operation_id}",
        "transaction": f"/transactions/{op['transaction_id']}",
    }
    return data


# ==========================================================
# Root
# ==========================================================
@app.get("/")
def root():
    return {"message": "Wallet/Cart/Transaction service running."}
