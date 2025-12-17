import json
import os
from google.cloud import pubsub_v1

PROJECT_ID = os.getenv("GCP_PROJECT_ID", "flipzy-475423")
TOPIC_ID = os.getenv("TX_COMPLETED_TOPIC", "transaction-completed")

publisher = pubsub_v1.PublisherClient()
topic_path = publisher.topic_path(PROJECT_ID, TOPIC_ID)


def publish_transaction_completed(tx):
    payload = {
        "transaction_id": tx.id,
        "buyer_id": tx.buyer_id,
        "seller_id": tx.seller_id,
        "item_id": tx.item_id,
        "order_type": tx.order_type,
        "price_snapshot": str(tx.price_snapshot),
        "status": tx.status.value,
        "completed_at": tx.created_at.isoformat(),
    }

    publisher.publish(
        topic_path,
        json.dumps(payload).encode("utf-8")
    )
