# Transaction Microservice

This microservice provides unified wallet management and transaction processing for a marketplace platform.
It supports:

- Wallet creation and balance updates
- Virtual item instant payments
- Real item asynchronous checkout
- Transaction listing and filtering
- HATEOAS-style resource links

All data is stored in-memory for simplicity.

## 1. Models

### Wallet
id: UUID
user_id: UUID          # Each user has exactly one wallet
balance: Decimal       # >= 0
created_at: datetime
updated_at: datetime

### Transaction
id: UUID
buyer_id: UUID
seller_id: UUID
item_id: UUID
order_type: "REAL" | "VIRTUAL"
title_snapshot: str
price_snapshot: Decimal
status: PENDING | COMPLETED | FAILED
created_at: datetime

#### Transaction Status
- PENDING
- COMPLETED
- FAILED

#### Behavior Summary
- VIRTUAL orders: processed immediately
- REAL orders: always start as PENDING → require `/checkout` to finalize

## 2. API Endpoints

## Wallet APIs

### Create Wallet  
POST /wallets

Request:
{
  "user_id": "UUID"
}

### List Wallets  
GET /wallets  
Optional filter: ?user_id=UUID

### Get Wallet  
GET /wallets/{wallet_id}

### Deposit  
POST /wallets/{wallet_id}/deposit

Request:
{ "amount": "100.00" }

### Delete Wallet  
DELETE /wallets/{wallet_id}

## Transaction APIs

### Create Transaction  
POST /transactions

Request:
{
  "buyer_id": "...",
  "seller_id": "...",
  "item_id": "...",
  "order_type": "REAL or VIRTUAL",
  "title_snapshot": "string",
  "price_snapshot": "decimal"
}

### List Transactions  
GET /transactions

### Get Transaction  
GET /transactions/{tx_id}

### Checkout Real Transaction  
POST /transactions/{tx_id}/checkout

## 3. Status Flow Summary

### Virtual Items
CREATE → COMPLETED / FAILED

### Real Items
CREATE (PENDING)
    ↓ checkout()
COMPLETED / FAILED

## 4. HATEOAS Links

Example wallet links:
{
  "self": "/wallets/{wallet_id}"
}

Example transaction links:
{
  "self": "/transactions/{tx_id}",
  "buyer_wallet": "/wallets/{buyer_wallet_id}",
  "seller_wallet": "/wallets/{seller_wallet_id}"
}

## 5. Notes for Other Microservices

- title_snapshot and price_snapshot come from the Composite Service
- This service does not validate existence of users/items
- It only validates wallets
- Virtual & real payments differ significantly

## 6. Health Check

GET /
{ "message": "Transaction Service running" }
