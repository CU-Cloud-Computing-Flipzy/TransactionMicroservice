from __future__ import annotations

import json
import hashlib
import threading
import time
from uuid import UUID, uuid4
from datetime import datetime
from decimal import Decimal
from typing import Dict, Optional, Any, Literal, List

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, condecimal

from models.wallet import Wallet
from models.transaction import Transaction, TransactionStatus


# ==========================================
# In-Memory Storage
# ==========================================

wallets: Dict[UUID, Wallet] = {}
wallet_by_user: Dict[UUID, UUID] = {}

transactions: Dict[UUID, Transaction] = {}

operations: Dict[UUID, Dict[str, Any]] = {}

products: Dict[UUID, Dict[str, Any]] = {}


# ==========================================
# Helper: ETag & Links
# ==========================================

def compute_etag(model: BaseModel) -> str:
    raw = json.dumps(model.model_dump(), sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def wallet_links(w: Wallet) -> Dict[str, str]:
    return {"self": f"/wallets/{w.id}"}


def tx_links(t: Transaction) -> Dict[str, str]:
    return {
        "self": f"/transactions/{t.id}",
        "buyer_wallet": f"/wallets/{wallet_by_user.get(t.buyer_id)}"
        if t.buyer_id in wallet_by_user else "",
        "seller_wallet": f"/wallets/{wallet_by_user.get(t.seller_id)}"
        if t.seller_id in wallet_by_user else "",
    }


def op_links(op_id: UUID, tx_id: UUID) -> Dict[str, str]:
    return {"self": f"/operations/{op_id}", "transaction": f"/transactions/{tx_id}"}


# ==========================================
# Request Models
# ==========================================

class WalletCreate(BaseModel):
    user_id: UUID


class DepositRequest(BaseModel):
    amount: condecimal(gt=0, max_digits=20, decimal_places=2)


class TransactionCreate(BaseModel):
    buyer_id: UUID
    seller_id: UUID
    item_id: UUID
    order_type: Literal["REAL", "VIRTUAL"]


# ==========================================
# Listing Service
# ==========================================

def get_product_snapshot(item_id: UUID) -> Dict[str, Any]:
    p = products.get(item_id)
    if not p:
        raise HTTPException(400, "Item not found in listing service")
    if "title" not in p or "price" not in p:
        raise HTTPException(500, "Invalid product data from listing service")
    return p


# ==========================================
# FastAPI App
# ==========================================

app = FastAPI(title="Simplified Transaction Service", version="1.0.0")


# ==========================================
# Wallet Endpoints
# ==========================================

@app.post("/wallets", status_code=201)
def create_wallet(req: WalletCreate, response: Response):
    if req.user_id in wallet_by_user:
        raise HTTPException(400, "User already has a wallet")

    w = Wallet(user_id=req.user_id)
    wallets[w.id] = w
    wallet_by_user[req.user_id] = w.id

    response.headers["Location"] = f"/wallets/{w.id}"
    data = w.model_dump()
    data["_links"] = wallet_links(w)
    return data


@app.get("/wallets")
def list_wallets(user_id: Optional[UUID] = None):
    ws = list(wallets.values())
    if user_id:
        ws = [w for w in ws if w.user_id == user_id]

    return [{**w.model_dump(), "_links": wallet_links(w)} for w in ws]


@app.get("/wallets/{wallet_id}")
def get_wallet(wallet_id: UUID, request: Request, response: Response):
    w = wallets.get(wallet_id)
    if not w:
        raise HTTPException(404, "Wallet not found")

    etag = compute_etag(w)

    if request.headers.get("If-None-Match") == etag:
        response.status_code = 304
        return

    response.headers["ETag"] = etag
    data = w.model_dump()
    data["_links"] = wallet_links(w)
    return data


@app.post("/wallets/{wallet_id}/deposit")
def deposit(wallet_id: UUID, req: DepositRequest):
    w = wallets.get(wallet_id)
    if not w:
        raise HTTPException(404, "Wallet not found")

    w.balance += req.amount
    w.updated_at = datetime.utcnow()

    data = w.model_dump()
    data["_links"] = wallet_links(w)
    return data


@app.delete("/wallets/{wallet_id}", status_code=204)
def delete_wallet(wallet_id: UUID):
    w = wallets.get(wallet_id)
    if not w:
        raise HTTPException(404, "Wallet not found")

    del wallets[wallet_id]

    if w.user_id in wallet_by_user:
        del wallet_by_user[w.user_id]

    return Response(status_code=204)


# ==========================================
# Transaction Endpoints
# ==========================================

@app.post("/transactions", status_code=201)
def create_transaction(req: TransactionCreate, response: Response):
    if req.buyer_id == req.seller_id:
        raise HTTPException(400, "Buyer and seller cannot be the same")

    snap = get_product_snapshot(req.item_id)
    title = snap["title"]
    price: Decimal = snap["price"]

    bwid = wallet_by_user.get(req.buyer_id)
    swid = wallet_by_user.get(req.seller_id)

    if not bwid or not swid:
        raise HTTPException(400, "Buyer or seller wallet not found")

    buyer_wallet = wallets[bwid]
    seller_wallet = wallets[swid]

    # Virtual — synchronous
    if req.order_type == "VIRTUAL":
        if buyer_wallet.balance < price:
            status = TransactionStatus.FAILED
        else:
            status = TransactionStatus.COMPLETED
            buyer_wallet.balance -= price
            seller_wallet.balance += price
            now = datetime.utcnow()
            buyer_wallet.updated_at = now
            seller_wallet.updated_at = now

        t = Transaction(
            buyer_id=req.buyer_id,
            seller_id=req.seller_id,
            item_id=req.item_id,
            order_type=req.order_type,
            title_snapshot=title,
            price_snapshot=price,
            status=status,
        )

        transactions[t.id] = t
        response.headers["Location"] = f"/transactions/{t.id}"
        data = t.model_dump()
        data["_links"] = tx_links(t)
        return data

    # Real — asynchronous
    t = Transaction(
        buyer_id=req.buyer_id,
        seller_id=req.seller_id,
        item_id=req.item_id,
        order_type=req.order_type,
        title_snapshot=title,
        price_snapshot=price,
        status=TransactionStatus.PENDING,
    )

    transactions[t.id] = t
    response.headers["Location"] = f"/transactions/{t.id}"

    data = t.model_dump()
    data["_links"] = tx_links(t)
    return data


@app.get("/transactions")
def list_transactions(
    buyer_id: Optional[UUID] = None,
    seller_id: Optional[UUID] = None,
    status: Optional[TransactionStatus] = None,
    page: int = 1,
    page_size: int = 10,
):
    results = list(transactions.values())

    if buyer_id:
        results = [t for t in results if t.buyer_id == buyer_id]
    if seller_id:
        results = [t for t in results if t.seller_id == seller_id]
    if status:
        results = [t for t in results if t.status == status]

    total = len(results)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = results[start:end]

    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": [
            {**t.model_dump(), "_links": tx_links(t)} for t in page_items
        ],
    }


@app.get("/transactions/{tx_id}")
def get_transaction(tx_id: UUID):
    t = transactions.get(tx_id)
    if not t:
        raise HTTPException(404, "Transaction not found")
    data = t.model_dump()
    data["_links"] = tx_links(t)
    return data


@app.put("/transactions/{tx_id}")
def update_transaction(tx_id: UUID, payload: Dict[str, Any]):
    t = transactions.get(tx_id)
    if not t:
        raise HTTPException(404, "Transaction not found")

    if "title_snapshot" in payload:
        t.title_snapshot = payload["title_snapshot"]

    data = t.model_dump()
    data["_links"] = tx_links(t)
    return data


@app.delete("/transactions/{tx_id}", status_code=204)
def delete_transaction(tx_id: UUID):
    if tx_id not in transactions:
        raise HTTPException(404, "Transaction not found")

    del transactions[tx_id]
    return Response(status_code=204)


@app.post("/transactions/{tx_id}/checkout", status_code=202)
def checkout_transaction(tx_id: UUID):
    t = transactions.get(tx_id)
    if not t:
        raise HTTPException(404, "Transaction not found")

    if t.order_type != "REAL":
        raise HTTPException(400, "Checkout only applies to REAL items")

    if t.status != TransactionStatus.PENDING:
        raise HTTPException(400, "Transaction already processed")

    def background_job():
        time.sleep(2)
        buyer_wallet = wallets[wallet_by_user[t.buyer_id]]
        seller_wallet = wallets[wallet_by_user[t.seller_id]]
        price = t.price_snapshot

        if buyer_wallet.balance < price:
            t.status = TransactionStatus.FAILED
        else:
            t.status = TransactionStatus.COMPLETED
            buyer_wallet.balance -= price
            seller_wallet.balance += price
            now = datetime.utcnow()
            buyer_wallet.updated_at = now
            seller_wallet.updated_at = now

    threading.Thread(target=background_job, daemon=True).start()

    data = t.model_dump()
    data["_links"] = tx_links(t)
    data["processing"] = True
    return data


# ==========================================
# Root
# ==========================================

@app.get("/")
def root():
    return {"message": "Simplified Transaction API running"}
