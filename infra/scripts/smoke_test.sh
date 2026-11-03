#!/usr/bin/env bash
# End-to-end smoke test against a running instance: ./infra/scripts/smoke_test.sh http://localhost:8000
set -euo pipefail
BASE="${1:-http://localhost:8000}"
RUN="$(date +%s)$RANDOM"
j() { python3 -c "import sys,json;print(json.load(sys.stdin)$1)"; }

echo "== health"
for i in $(seq 1 30); do curl -fsS "$BASE/health/live" >/dev/null && break; sleep 2; done
curl -fsS "$BASE/health/ready" | tee /dev/stderr | grep -q '"status"'
echo

signup() {
  curl -fsS -X POST "$BASE/api/auth/signup" -H 'Content-Type: application/json' \
    -d "{\"email\":\"$1-$RUN@example.com\",\"password\":\"Sm0keTest!\",\"full_name\":\"Smoke $1\",\"default_currency\":\"$2\"}" | j "['access_token']"
}
echo "== signup"
A=$(signup alice USD); B=$(signup bob EUR)

echo "== credit 100 USD"
curl -fsS -X POST "$BASE/api/wallets/credit" -H "Authorization: Bearer $A" -H 'Content-Type: application/json' \
  -H "Idempotency-Key: smoke-credit-$RUN" -d '{"amount":"100","currency":"USD"}' >/dev/null

echo "== transfer 25 USD -> bob (EUR)"
curl -fsS -X POST "$BASE/api/transfers" -H "Authorization: Bearer $A" -H 'Content-Type: application/json' \
  -H "Idempotency-Key: smoke-transfer-$RUN" \
  -d "{\"recipient_email\":\"bob-$RUN@example.com\",\"amount\":\"25\",\"currency\":\"USD\"}" | j "['target_amount']"

echo "== balances / history"
curl -fsS "$BASE/api/wallets" -H "Authorization: Bearer $A" | j "['wallets'][0]['balance']" | python3 -c "import sys,decimal;assert decimal.Decimal(sys.stdin.read())==75, 'bad balance'"
curl -fsS "$BASE/api/transactions?page_size=5" -H "Authorization: Bearer $B" | j "['total']" | grep -qx '1'

echo "== insufficient funds is a 4xx, not a 5xx"
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/wallets/debit" -H "Authorization: Bearer $A" \
  -H 'Content-Type: application/json' -d '{"amount":"1000000","currency":"USD"}')
[ "$code" = "422" ] || [ "$code" = "409" ] || { echo "unexpected $code"; exit 1; }
echo "smoke test passed"
