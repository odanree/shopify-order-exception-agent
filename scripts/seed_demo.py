#!/usr/bin/env python3
"""Demo seed script for the Order Exception Agent.

Sends a batch of realistic Shopify-style order webhook payloads to the local
service so you can watch the full pipeline execute without a live Shopify store.

Usage:
    python scripts/seed_demo.py                     # sends all scenarios
    python scripts/seed_demo.py --scenario fraud    # single scenario
    python scripts/seed_demo.py --url http://host:8000

Requires:
    pip install httpx

The service must be running with SHOPIFY_WEBHOOK_SECRET="" (empty = HMAC skip).
"""
import argparse
import asyncio
import json
import time
import uuid
import sys

try:
    import httpx
except ImportError:
    print("pip install httpx")
    sys.exit(1)

BASE_URL = "http://localhost:8000"
WEBHOOK_PATH = "/api/webhooks/orders/create"

# ---------------------------------------------------------------------------
# Demo scenarios
# ---------------------------------------------------------------------------

SCENARIOS = {
    "fraud": {
        "name": "Fraud Risk — international billing / high-risk score",
        "payload": {
            "id": 5001000001,
            "order_number": 1001,
            "total_price": "247.00",
            "financial_status": "paid",
            "fulfillment_status": None,
            "risk_level": "HIGH",
            "tags": "",
            "shipping_address": {
                "first_name": "John", "last_name": "Smith",
                "address1": "123 Unknown Rd", "city": "Lagos",
                "province": "", "zip": "10001", "country": "NG", "phone": ""
            },
            "customer": {"id": 9001, "email": "jsmith@tempmail.io"},
        },
    },
    "address": {
        "name": "Invalid Address — missing city/zip",
        "payload": {
            "id": 5001000002,
            "order_number": 1002,
            "total_price": "89.50",
            "financial_status": "paid",
            "fulfillment_status": None,
            "risk_level": "LOW",
            "tags": "",
            "shipping_address": {
                "first_name": "Alice", "last_name": "Johnson",
                "address1": "", "city": "", "province": "",
                "zip": "", "country": "US", "phone": ""
            },
            "customer": {"id": 9002, "email": "alice@example.com"},
        },
    },
    "high_value": {
        "name": "High-Value — $1,450 order (above $500 threshold → manual review)",
        "payload": {
            "id": 5001000003,
            "order_number": 1003,
            "total_price": "1450.00",
            "financial_status": "paid",
            "fulfillment_status": None,
            "risk_level": "LOW",
            "tags": "",
            "shipping_address": {
                "first_name": "Bob", "last_name": "Williams",
                "address1": "456 Oak Ave", "city": "Austin",
                "province": "TX", "zip": "78701", "country": "US", "phone": "5125550100"
            },
            "customer": {"id": 9003, "email": "bob@example.com"},
        },
    },
    "payment": {
        "name": "Payment Issue — failed authorization",
        "payload": {
            "id": 5001000004,
            "order_number": 1004,
            "total_price": "156.00",
            "financial_status": "payment_pending",
            "fulfillment_status": None,
            "risk_level": "MEDIUM",
            "tags": "payment-failed",
            "shipping_address": {
                "first_name": "Carol", "last_name": "Davis",
                "address1": "789 Pine St", "city": "Seattle",
                "province": "WA", "zip": "98101", "country": "US", "phone": ""
            },
            "customer": {"id": 9004, "email": "carol@example.com"},
        },
    },
    "fulfillment_delay": {
        "name": "Fulfillment Delay — order stuck unfulfilled",
        "payload": {
            "id": 5001000005,
            "order_number": 1005,
            "total_price": "67.99",
            "financial_status": "paid",
            "fulfillment_status": "unfulfilled",
            "risk_level": "LOW",
            "tags": "delayed",
            "shipping_address": {
                "first_name": "Dave", "last_name": "Lee",
                "address1": "321 Elm St", "city": "Chicago",
                "province": "IL", "zip": "60601", "country": "US", "phone": ""
            },
            "customer": {"id": 9005, "email": "dave@example.com"},
        },
    },
    "unknown": {
        "name": "Unknown — minimal payload (tests classifier fallback)",
        "payload": {
            "id": 5001000006,
            "order_number": 1006,
            "total_price": "12.00",
            "financial_status": "paid",
            "fulfillment_status": "fulfilled",
            "risk_level": "LOW",
            "tags": "",
            "shipping_address": {"country": "US"},
            "customer": {"id": 9006, "email": "unknown@example.com"},
        },
    },
}


async def send_webhook(client: httpx.AsyncClient, base_url: str, scenario_key: str, scenario: dict):
    payload = dict(scenario["payload"])
    webhook_id = f"demo-{scenario_key}-{uuid.uuid4().hex[:8]}"
    headers = {
        "X-Shopify-Topic": "orders/create",
        "X-Shopify-Shop-Domain": "demo-store.myshopify.com",
        "X-Shopify-Webhook-Id": webhook_id,
        "Content-Type": "application/json",
    }
    try:
        resp = await client.post(
            f"{base_url}{WEBHOOK_PATH}",
            content=json.dumps(payload),
            headers=headers,
            timeout=10.0,
        )
        status = "✅" if resp.status_code == 200 else "❌"
        result = resp.json()
        print(f"  {status} [{resp.status_code}] {scenario['name']}")
        print(f"     webhook_id={webhook_id} action={result.get('action', '?')}")
    except Exception as exc:
        print(f"  ❌ {scenario['name']}: {exc}")


async def main():
    parser = argparse.ArgumentParser(description="Seed demo order webhooks")
    parser.add_argument("--url", default=BASE_URL, help="Base URL of the agent service")
    parser.add_argument("--scenario", choices=list(SCENARIOS.keys()), help="Run a single scenario")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds between requests")
    args = parser.parse_args()

    scenarios = {args.scenario: SCENARIOS[args.scenario]} if args.scenario else SCENARIOS

    print("\n🤖 Order Exception Agent — Demo Seed")
    print(f"   Target: {args.url}")
    print(f"   Scenarios: {len(scenarios)}\n")

    async with httpx.AsyncClient() as client:
        # Verify the service is up
        try:
            health = await client.get(f"{args.url}/health", timeout=5.0)
            status = health.json().get("status", "unknown")
            print(f"   /health → {status}\n")
        except Exception as exc:
            print(f"   ⚠ Could not reach {args.url}/health: {exc}")
            print("   Make sure the service is running: docker-compose up\n")

        for key, scenario in scenarios.items():
            await send_webhook(client, args.url, key, scenario)
            await asyncio.sleep(args.delay)

    print(f"\n   Done. Watch the dashboard: {args.url}/dashboard\n")


if __name__ == "__main__":
    asyncio.run(main())
