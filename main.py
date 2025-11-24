from __future__ import annotations

import json
import hashlib
import threading
import time
from uuid import UUID
from datetime import datetime
from decimal import Decimal
from typing import Optional, Literal

from fastapi import FastAPI, HTTPException, Request, Response, Depends
from pydantic import BaseModel, condecimal
from sqlalchemy.orm import Session

from db import (
    WalletSQL,
    TransactionSQL,
    TransactionStatus,
    get_db,
    init_db,
)

# ---------------------------------------------------------
# FastAPI
# ---------------------------------------------------------
app = FastAPI(title="Transaction Service", version="2.0.0")


# ---------------------------------------------------------
# ETag helper
# ---------------------------------------------------------
def compute_etag(data: dict) -> str:
    """Compute ETag hash from response data."""
    raw = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------
# Link helpers
# ---------------------------------------------------------
def wallet_links(w: WalletSQL):
    return {"self": f"/wallets/{w.id}"}


def tx_links(t: TransactionSQL):
    return {
        "self": f"/transactions/{t.id}",
        "buyer_wallet": f"/wallets/{t.buyer_id}",
        "seller_wallet": f"/wallets/{t.seller_id}",
    }


# ---------------------------------------------------------
# Request models
# ---------------------------------------------------------
class WalletCreate(BaseModel):
    """Create new wallet (1 per user)."""
    user_id: UUID


class DepositRequest(BaseModel):
    """Deposit amount into a wallet."""
    amount: condecimal(gt=0, max_digits=20, decimal_places=2)


class TransactionCreate(BaseModel):
    """Create a new transaction."""
    buyer_id: UUID
    seller_id: UUID
    item_id: UUID
    order_type: Literal["REAL", "VIRTUAL"]
    title_snapshot: str
    price_snapshot: condecimal(gt=0, max_digits=20, decimal_places=2)


# ---------------------------------------------------------
# Wallet Endpoints
# ---------------------------------------------------------

@app.post("/wallets", status_code=201)
def create_wallet(req: WalletCreate, response: Response, db: Session = Depends(get_db)):
    """Create a wallet. User can only have 1 wallet."""
    existing = db.query(WalletSQL).filter(WalletSQL.user_id == str(req.user_id)).first()
    if existing:
        raise HTTPException(400, "User already has a wallet")

    w = WalletSQL(user_id=str(req.user_id))
    db.add(w)
    db.commit()
    db.refresh(w)

    response.headers["Location"] = f"/wallets/{w.id}"
    return {**wallet_to_dict(w), "_links": wallet_links(w)}


@app.get("/wallets")
def list_wallets(user_id: Optional[UUID] = None, db: Session = Depends(get_db)):
    """List wallets, optionally filter by user."""
    query = db.query(WalletSQL)
    if user_id:
        query = query.filter(WalletSQL.user_id == str(user_id))
    wallets = query.all()
    return [{**wallet_to_dict(w), "_links": wallet_links(w)} for w in wallets]


@app.get("/wallets/{wallet_id}")
def get_wallet(wallet_id: UUID, request: Request, response: Response, db: Session = Depends(get_db)):
    """Get wallet by ID with ETag support."""
    w = db.query(WalletSQL).filter(WalletSQL.id == str(wallet_id)).first()
    if not w:
        raise HTTPException(404, "Wallet not found")

    data = wallet_to_dict(w)
    etag = compute_etag(data)

    if request.headers.get("If-None-Match") == etag:
        response.status_code = 304
        return

    response.headers["ETag"] = etag
    return {**data, "_links": wallet_links(w)}


@app.post("/wallets/{wallet_id}/deposit")
def deposit(wallet_id: UUID, req: DepositRequest, db: Session = Depends(get_db)):
    """Deposit funds into a wallet."""
    w = db.query(WalletSQL).filter(WalletSQL.id == str(wallet_id)).first()
    if not w:
        raise HTTPException(404, "Wallet not found")

    w.balance = Decimal(w.balance) + Decimal(req.amount)
    w.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(w)
    return {**wallet_to_dict(w), "_links": wallet_links(w)}


@app.put("/wallets/{wallet_id}")
def update_wallet(wallet_id: UUID, req: WalletCreate, db: Session = Depends(get_db)):
    """Update wallet user_id (CRUD completeness)."""
    w = db.query(WalletSQL).filter(WalletSQL.id == str(wallet_id)).first()
    if not w:
        raise HTTPException(404, "Wallet not found")

    w.user_id = str(req.user_id)
    w.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(w)
    return {**wallet_to_dict(w), "_links": wallet_links(w)}


@app.delete("/wallets/{wallet_id}", status_code=204)
def delete_wallet(wallet_id: UUID, db: Session = Depends(get_db)):
    """Delete a wallet."""
    w = db.query(WalletSQL).filter(WalletSQL.id == str(wallet_id)).first()
    if not w:
        raise HTTPException(404, "Wallet not found")

    db.delete(w)
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------
# Transaction Endpoints
# ---------------------------------------------------------

@app.post("/transactions", status_code=201)
def create_transaction(req: TransactionCreate, response: Response, db: Session = Depends(get_db)):
    """Create a new transaction (REAL or VIRTUAL)."""

    if req.buyer_id == req.seller_id:
        raise HTTPException(400, "Buyer and seller cannot be the same")

    # Load wallets
    bw = db.query(WalletSQL).filter(WalletSQL.user_id == str(req.buyer_id)).first()
    sw = db.query(WalletSQL).filter(WalletSQL.user_id == str(req.seller_id)).first()
    if not bw or not sw:
        raise HTTPException(400, "Buyer or seller wallet not found")

    # VIRTUAL = instant settlement
    if req.order_type == "VIRTUAL":
        if Decimal(bw.balance) < Decimal(req.price_snapshot):
            status = TransactionStatus.FAILED
        else:
            status = TransactionStatus.COMPLETED
            bw.balance = Decimal(bw.balance) - Decimal(req.price_snapshot)
            sw.balance = Decimal(sw.balance) + Decimal(req.price_snapshot)
            now = datetime.utcnow()
            bw.updated_at = now
            sw.updated_at = now

        t = TransactionSQL(
            buyer_id=str(req.buyer_id),
            seller_id=str(req.seller_id),
            item_id=str(req.item_id),
            order_type=req.order_type,
            title_snapshot=req.title_snapshot,
            price_snapshot=req.price_snapshot,
            status=status,
        )
        db.add(t)
        db.commit()
        db.refresh(t)

        response.headers["Location"] = f"/transactions/{t.id}"
        return {**tx_to_dict(t), "_links": tx_links(t)}

    # REAL = async checkout
    t = TransactionSQL(
        buyer_id=str(req.buyer_id),
        seller_id=str(req.seller_id),
        item_id=str(req.item_id),
        order_type=req.order_type,
        title_snapshot=req.title_snapshot,
        price_snapshot=req.price_snapshot,
        status=TransactionStatus.PENDING,
    )

    db.add(t)
    db.commit()
    db.refresh(t)

    response.headers["Location"] = f"/transactions/{t.id}"
    return {**tx_to_dict(t), "_links": tx_links(t)}


@app.get("/transactions")
def list_transactions(
    buyer_id: Optional[UUID] = None,
    seller_id: Optional[UUID] = None,
    status: Optional[TransactionStatus] = None,
    db: Session = Depends(get_db)
):
    """List transactions with optional filters."""
    query = db.query(TransactionSQL)

    if buyer_id:
        query = query.filter(TransactionSQL.buyer_id == str(buyer_id))
    if seller_id:
        query = query.filter(TransactionSQL.seller_id == str(seller_id))
    if status:
        query = query.filter(TransactionSQL.status == status)

    txs = query.all()
    return [{**tx_to_dict(t), "_links": tx_links(t)} for t in txs]


@app.get("/transactions/{tx_id}")
def get_transaction(tx_id: UUID, db: Session = Depends(get_db)):
    """Get a transaction by ID."""
    t = db.query(TransactionSQL).filter(TransactionSQL.id == str(tx_id)).first()
    if not t:
        raise HTTPException(404, "Transaction not found")
    return {**tx_to_dict(t), "_links": tx_links(t)}


class TransactionUpdate(BaseModel):
    """Fields allowed to update (CRUD completeness)."""
    title_snapshot: Optional[str] = None
    price_snapshot: Optional[condecimal(gt=0, max_digits=20, decimal_places=2)] = None
    status: Optional[TransactionStatus] = None


@app.put("/transactions/{tx_id}")
def update_transaction(tx_id: UUID, req: TransactionUpdate, db: Session = Depends(get_db)):
    """Update a transaction (title, price, status)."""
    t = db.query(TransactionSQL).filter(TransactionSQL.id == str(tx_id)).first()
    if not t:
        raise HTTPException(404, "Transaction not found")

    if req.title_snapshot is not None:
        t.title_snapshot = req.title_snapshot
    if req.price_snapshot is not None:
        t.price_snapshot = req.price_snapshot
    if req.status is not None:
        t.status = req.status

    db.commit()
    db.refresh(t)
    return {**tx_to_dict(t), "_links": tx_links(t)}


@app.delete("/transactions/{tx_id}", status_code=204)
def delete_transaction(tx_id: UUID, db: Session = Depends(get_db)):
    """Delete a transaction."""
    t = db.query(TransactionSQL).filter(TransactionSQL.id == str(tx_id)).first()
    if not t:
        raise HTTPException(404, "Transaction not found")

    db.delete(t)
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------
# Checkout (Async) for REAL Items
# ---------------------------------------------------------
@app.post("/transactions/{tx_id}/checkout", status_code=202)
def checkout_transaction(tx_id: UUID, db: Session = Depends(get_db)):
    """Async checkout for REAL items."""
    t = db.query(TransactionSQL).filter(TransactionSQL.id == str(tx_id)).first()
    if not t:
        raise HTTPException(404, "Transaction not found")
    if t.order_type != "REAL":
        raise HTTPException(400, "Only applies to REAL items")
    if t.status != TransactionStatus.PENDING:
        raise HTTPException(400, "Transaction already processed")

    def job():
        time.sleep(2)
        session = next(get_db())

        tx_obj = session.query(TransactionSQL).filter(TransactionSQL.id == str(tx_id)).first()
        if not tx_obj:
            return

        bw = session.query(WalletSQL).filter(WalletSQL.user_id == tx_obj.buyer_id).first()
        sw = session.query(WalletSQL).filter(WalletSQL.user_id == tx_obj.seller_id).first()

        if Decimal(bw.balance) < Decimal(tx_obj.price_snapshot):
            tx_obj.status = TransactionStatus.FAILED
        else:
            tx_obj.status = TransactionStatus.COMPLETED
            bw.balance = Decimal(bw.balance) - Decimal(tx_obj.price_snapshot)
            sw.balance = Decimal(sw.balance) + Decimal(tx_obj.price_snapshot)
            now = datetime.utcnow()
            bw.updated_at = now
            sw.updated_at = now

        session.commit()

    threading.Thread(target=job, daemon=True).start()
    return {**tx_to_dict(t), "_links": tx_links(t), "processing": True}


# ---------------------------------------------------------
# Helper: ORM → dict
# ---------------------------------------------------------
def wallet_to_dict(w: WalletSQL):
    return {
        "id": w.id,
        "user_id": w.user_id,
        "balance": str(w.balance),
        "created_at": w.created_at,
        "updated_at": w.updated_at,
    }


def tx_to_dict(t: TransactionSQL):
    return {
        "id": t.id,
        "buyer_id": t.buyer_id,
        "seller_id": t.seller_id,
        "item_id": t.item_id,
        "order_type": t.order_type,
        "title_snapshot": t.title_snapshot,
        "price_snapshot": str(t.price_snapshot),
        "status": t.status.value,
        "created_at": t.created_at,
    }


# ---------------------------------------------------------
# Root
# ---------------------------------------------------------
@app.get("/")
def root():
    return {"message": "Transaction Service with MySQL (Cloud SQL) running"}


# ---------------------------------------------------------
# INIT DB
# ---------------------------------------------------------
init_db()
