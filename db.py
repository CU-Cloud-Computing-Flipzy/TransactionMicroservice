from __future__ import annotations

import os
from datetime import datetime
import uuid

from sqlalchemy import (
    Column,
    String,
    DateTime,
    Numeric,
    Enum,
    create_engine,
)
from sqlalchemy.dialects.mysql import CHAR
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from enum import Enum as PyEnum


# ============================================================================
# Cloud SQL Connection
# ============================================================================

DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME", "transaction-service")
DB_HOST = os.getenv("DB_HOST")

if DB_HOST:
    # Cloud Run → Unix socket
    SQLALCHEMY_DATABASE_URL = (
        f"mysql+pymysql://{DB_USER}:{DB_PASS}@/{DB_NAME}"
        f"?unix_socket={DB_HOST}"
    )
else:
    # Local development
    DB_PORT = os.getenv("DB_PORT", "3306")
    DB_HOSTNAME = os.getenv("DB_HOSTNAME", "127.0.0.1")
    SQLALCHEMY_DATABASE_URL = (
        f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOSTNAME}:{DB_PORT}/{DB_NAME}"
    )

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=3600,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


# ============================================================================
# Enum for status
# ============================================================================

class TransactionStatus(PyEnum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# ============================================================================
# ORM Models
# ============================================================================

def gen_uuid() -> str:
    """Generate UUID string for SQL CHAR(36)."""
    return str(uuid.uuid4())


# --------------------------
# Wallet Table
# --------------------------
class WalletSQL(Base):
    __tablename__ = "wallets"

    id = Column(CHAR(36), primary_key=True, default=gen_uuid)
    user_id = Column(CHAR(36), unique=True, nullable=False)

    balance = Column(Numeric(20, 2), nullable=False, default=0)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


# --------------------------
# Transactions Table
# --------------------------
class TransactionSQL(Base):
    __tablename__ = "transactions"

    id = Column(CHAR(36), primary_key=True, default=gen_uuid)

    buyer_id = Column(CHAR(36), nullable=False)
    seller_id = Column(CHAR(36), nullable=False)
    item_id = Column(CHAR(36), nullable=False)

    order_type = Column(String(16), nullable=False)  # "REAL" / "VIRTUAL"

    title_snapshot = Column(String(255), nullable=False)
    price_snapshot = Column(Numeric(20, 2), nullable=False)

    status = Column(Enum(TransactionStatus), nullable=False, default=TransactionStatus.PENDING)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


# ============================================================================
# Database Helpers
# ============================================================================

def get_db() -> Session:
    """Session dependency for FastAPI."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables if they do not exist."""
    Base.metadata.create_all(bind=engine)
