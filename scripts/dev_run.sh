#!/usr/bin/env bash
# Dev dry-run script for the Shopify Order Exception Agent.
#
# Usage:
#   ./scripts/dev_run.sh
#   ./scripts/dev_run.sh --send-test-webhook
#
# Prerequisites:
#   - ngrok v3 installed (https://ngrok.com/download)
#   - .env file present with SHOPIFY_SANDBOX_MODE=true and dev store credentials
#   - App running locally on port 8000 (docker-compose up or uvicorn)
set -euo pipefail

# ---------------------------------------------------------------------------
# Guard checks
# ---------------------------------------------------------------------------
if [[ ! -f ".env" ]]; then
  echo "ERROR: .env file not found. Copy .env.example and fill in dev store values." >&2
  exit 1
fi

# Load .env (simple key=value lines, no export needed)
set -a
source .env 2>/dev/null || true
set +a

if [[ "${SHOPIFY_SANDBOX_MODE:-false}" != "true" ]]; then
  echo "ERROR: SHOPIFY_SANDBOX_MODE must be 'true' to use dev_run.sh." >&2
  echo "       Set it in .env to protect your production store." >&2
  exit 1
fi

if [[ -z "${SHOPIFY_DEV_STORE_DOMAIN:-}" ]]; then
  echo "ERROR: SHOPIFY_DEV_STORE_DOMAIN not set in .env" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Parse flags
# ---------------------------------------------------------------------------
SEND_TEST=false
for arg in "$@"; do
  case "$arg" in
    --send-test-webhook) SEND_TEST=true ;;
  esac
done

# ---------------------------------------------------------------------------
# Find ngrok binary (works on macOS, Linux, WSL)
# ---------------------------------------------------------------------------
NGROK_BIN=""
if command -v ngrok &>/dev/null; then
  NGROK_BIN="ngrok"
elif command -v ngrok.exe &>/dev/null; then
  NGROK_BIN="ngrok.exe"
else
  echo "ERROR: ngrok not found. Install from https://ngrok.com/download" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Start ngrok
# ---------------------------------------------------------------------------
echo "Starting ngrok tunnel on port 8000..."
"$NGROK_BIN" http 8000 --log stdout >/tmp/ngrok-order-exception.log 2>&1 &
NGROK_PID=$!
trap 'kill $NGROK_PID 2>/dev/null; echo "ngrok stopped."' EXIT

# Wait for ngrok API to be ready
for i in {1..10}; do
  PUBLIC_URL=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['tunnels'][0]['public_url'])" 2>/dev/null || true)
  if [[ -n "$PUBLIC_URL" ]]; then
    break
  fi
  sleep 1
done

if [[ -z "$PUBLIC_URL" ]]; then
  echo "ERROR: Could not get ngrok public URL. Check /tmp/ngrok-order-exception.log" >&2
  exit 1
fi

echo "ngrok tunnel:  $PUBLIC_URL"
echo "Dev store:     $SHOPIFY_DEV_STORE_DOMAIN"
echo ""

# ---------------------------------------------------------------------------
# Register webhooks against dev store
# ---------------------------------------------------------------------------
echo "Registering webhooks..."
WEBHOOK_ENDPOINT_URL="$PUBLIC_URL" python3 scripts/register_webhooks.py
echo ""

# ---------------------------------------------------------------------------
# Print manual test curl
# ---------------------------------------------------------------------------
SAMPLE_ID=$((RANDOM * RANDOM))
echo "To send a test order/create webhook manually:"
echo ""
echo "  PAYLOAD='{\"id\":$SAMPLE_ID,\"total_price\":\"1250.00\",\"financial_status\":\"paid\",\"fulfillment_status\":null,\"risk_level\":\"HIGH\",\"shipping_address\":{\"country\":\"US\",\"address1\":\"123 Main St\",\"city\":\"New York\",\"zip\":\"10001\"},\"tags\":\"\"}'"
echo "  WEBHOOK_ID=\"test-\$(date +%s)\""
echo "  SIG=\$(python3 -c \"import hmac,hashlib,base64,os; print(base64.b64encode(hmac.new(os.environ['SHOPIFY_WEBHOOK_SECRET'].encode(),'\$PAYLOAD'.encode(),hashlib.sha256).digest()).decode())\")"
echo "  curl -X POST $PUBLIC_URL/api/webhooks/orders/create \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -H \"X-Shopify-Hmac-Sha256: \$SIG\" \\"
echo "    -H \"X-Shopify-Webhook-Id: \$WEBHOOK_ID\" \\"
echo "    -H 'X-Shopify-Topic: orders/create' \\"
echo "    -d \"\$PAYLOAD\""
echo ""

# ---------------------------------------------------------------------------
# --send-test-webhook: fire one automatically
# ---------------------------------------------------------------------------
if [[ "$SEND_TEST" == "true" ]]; then
  if [[ -z "${SHOPIFY_WEBHOOK_SECRET:-}" ]]; then
    echo "ERROR: SHOPIFY_WEBHOOK_SECRET not set — cannot compute HMAC for test webhook." >&2
    exit 1
  fi

  PAYLOAD="{\"id\":$SAMPLE_ID,\"total_price\":\"1250.00\",\"financial_status\":\"paid\",\"fulfillment_status\":null,\"risk_level\":\"HIGH\",\"shipping_address\":{\"country\":\"US\",\"address1\":\"123 Main St\",\"city\":\"New York\",\"zip\":\"10001\"},\"tags\":\"\"}"
  WEBHOOK_ID="test-$(date +%s)"

  SIG=$(python3 -c "
import hmac as _hmac, hashlib, base64, os
secret = os.environ['SHOPIFY_WEBHOOK_SECRET'].encode()
body = '''$PAYLOAD'''.encode()
print(base64.b64encode(_hmac.new(secret, body, hashlib.sha256).digest()).decode())
")

  echo "Sending test webhook (id=$WEBHOOK_ID)..."
  RESPONSE=$(curl -s -w "\nHTTP_STATUS:%{http_code}" -X POST "$PUBLIC_URL/api/webhooks/orders/create" \
    -H "Content-Type: application/json" \
    -H "X-Shopify-Hmac-Sha256: $SIG" \
    -H "X-Shopify-Webhook-Id: $WEBHOOK_ID" \
    -H "X-Shopify-Topic: orders/create" \
    -d "$PAYLOAD")

  HTTP_STATUS=$(echo "$RESPONSE" | grep "HTTP_STATUS:" | cut -d: -f2)
  BODY=$(echo "$RESPONSE" | grep -v "HTTP_STATUS:")

  echo "Response ($HTTP_STATUS): $BODY"

  if [[ "$HTTP_STATUS" == "200" ]]; then
    echo "Webhook accepted. Check app logs for agent execution."
  else
    echo "ERROR: Unexpected status $HTTP_STATUS" >&2
  fi
fi

# ---------------------------------------------------------------------------
# Keep ngrok alive until Ctrl+C
# ---------------------------------------------------------------------------
echo "Press Ctrl+C to stop ngrok and exit."
wait $NGROK_PID
