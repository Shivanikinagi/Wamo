#!/usr/bin/env bash

set -u

BASE="${BASE:-http://localhost:8000}"
CUSTOMER="${CUSTOMER:-cust_rajesh_001}"
AGENT="${AGENT:-officer_priya}"
WAL_PATH="${WAL_PATH:-/home/parth/ccode/wam0/PS01/data/wal/ps01_wal.jsonl}"
MEM0_DB_CANDIDATE_A="/home/parth/ccode/wam0/PS01/mem0_history/default/default.db"
MEM0_DB_CANDIDATE_B="/home/parth/ccode/wam0/mem0_history/default/default.db"
CHROMA_DB_CANDIDATE_A="/home/parth/ccode/wam0/PS01/chroma_db/default/chroma.sqlite3"
CHROMA_DB_CANDIDATE_B="/home/parth/ccode/wam0/chroma_db/default/chroma.sqlite3"

if [[ -f "$MEM0_DB_CANDIDATE_A" ]]; then
  MEM0_DB="$MEM0_DB_CANDIDATE_A"
else
  MEM0_DB="$MEM0_DB_CANDIDATE_B"
fi

if [[ -f "$CHROMA_DB_CANDIDATE_A" ]]; then
  CHROMA_DB="$CHROMA_DB_CANDIDATE_A"
else
  CHROMA_DB="$CHROMA_DB_CANDIDATE_B"
fi

MAX_TIME="${MAX_TIME:-90}"
RETRIES="${RETRIES:-2}"

print_header() {
  echo "=============================="
  echo " $1"
  echo "=============================="
}

http_post_json() {
  local path="$1"
  local payload="$2"
  local attempt=0
  local out=""
  while [[ $attempt -le $RETRIES ]]; do
    out=$(curl -sS --max-time "$MAX_TIME" -X POST "$BASE$path" \
      -H "Content-Type: application/json" \
      -d "$payload")
    local code=$?
    if [[ $code -eq 0 ]]; then
      echo "$out"
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 1
  done
  return 1
}

count_wal() {
  if [[ -f "$WAL_PATH" ]]; then
    wc -l < "$WAL_PATH" | tr -d ' '
  else
    echo "0"
  fi
}

count_mem0_history() {
  if [[ -f "$MEM0_DB" ]]; then
    sqlite3 "$MEM0_DB" "SELECT COUNT(*) FROM history;" 2>/dev/null || echo "0"
  else
    echo "0"
  fi
}

count_chroma_embeddings() {
  if [[ -f "$CHROMA_DB" ]]; then
    sqlite3 "$CHROMA_DB" "SELECT COUNT(*) FROM embeddings;" 2>/dev/null || echo "0"
  else
    echo "0"
  fi
}

count_redis_keys() {
  redis-cli -p 6379 DBSIZE 2>/dev/null || echo "0"
}

snapshot_storage() {
  local tag="$1"
  local wal_count mem0_count chroma_count redis_count
  wal_count="$(count_wal)"
  mem0_count="$(count_mem0_history)"
  chroma_count="$(count_chroma_embeddings)"
  redis_count="$(count_redis_keys)"

  echo "$tag|$wal_count|$mem0_count|$chroma_count|$redis_count"
}

render_snapshot() {
  local label="$1"
  local snap="$2"
  IFS='|' read -r _ wal mem0 chroma redis <<< "$snap"
  echo "$label"
  echo "  WAL entries:        $wal"
  echo "  Mem0 history rows:  $mem0"
  echo "  Chroma embeddings:  $chroma"
  echo "  Redis key count:    $redis"
}

print_header "PS-01 EVAL RUNNER (REAL WIRING CLI)"

echo "API base:    $BASE"
echo "Customer:    $CUSTOMER"
echo "Agent:       $AGENT"
echo "WAL path:    $WAL_PATH"
echo "Mem0 DB:     $MEM0_DB"
echo "Chroma DB:   $CHROMA_DB"
echo ""

# 0) Health check
print_header "0) Health check"
HEALTH=$(curl -sS --max-time 5 "$BASE/health") || {
  echo "FAIL: API health check failed"
  exit 1
}
echo "$HEALTH" | python3 -m json.tool || echo "$HEALTH"

BEFORE_SNAPSHOT="$(snapshot_storage BEFORE)"
echo ""
render_snapshot "Storage BEFORE:" "$BEFORE_SNAPSHOT"

# 1) Seed session start
print_header "1) Start seed session"
SEED_PAYLOAD="{\"customer_id\":\"$CUSTOMER\",\"session_type\":\"home_loan_processing\",\"agent_id\":\"$AGENT\",\"consent_id\":\"consent_rajesh_001\"}"
SEED_RESP="$(http_post_json "/session/start" "$SEED_PAYLOAD")" || {
  echo "FAIL: /session/start failed"
  exit 1
}
echo "$SEED_RESP" | python3 -m json.tool
SEED_ID="$(echo "$SEED_RESP" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("session_id",""))')"
if [[ -z "$SEED_ID" ]]; then
  echo "FAIL: seed session_id missing"
  exit 1
fi
echo "Seed session_id: $SEED_ID"

# 2) Add historical facts via memory/add
print_header "2) Add historical facts (memory/add)"
MEM_PAYLOAD=$(cat <<JSON
{
  "session_id": "$SEED_ID",
  "customer_id": "$CUSTOMER",
  "agent_id": "$AGENT",
  "facts": [
    {"type": "income",       "value": "55000",  "verified": false, "source": "customer_verbal"},
    {"type": "co_applicant", "value": "Sunita", "verified": false, "source": "customer_verbal"},
    {"type": "co_income",    "value": "30000",  "verified": false, "source": "customer_verbal"},
    {"type": "property",     "value": "Nashik", "verified": false, "source": "customer_verbal"},
    {"type": "emi_existing", "value": "8000",   "verified": false, "source": "customer_verbal"}
  ]
}
JSON
)
MEM_RESP="$(http_post_json "/memory/add" "$MEM_PAYLOAD")" || {
  echo "FAIL: /memory/add failed"
  exit 1
}
echo "$MEM_RESP" | python3 -m json.tool

# 3) End seed session
print_header "3) End seed session"
END_SEED="$(http_post_json "/session/end" "{\"session_id\":\"$SEED_ID\"}")" || {
  echo "WARN: /session/end timed out for seed"
  END_SEED='{}'
}
echo "$END_SEED" | python3 -m json.tool
sleep 2

# 4) New session opening
print_header "4) Opening statement"
OPEN_PAYLOAD="{\"customer_id\":\"$CUSTOMER\",\"session_type\":\"home_loan_processing\",\"agent_id\":\"$AGENT\",\"consent_id\":\"consent_rajesh_002\"}"
SESSION_RESP="$(http_post_json "/session/start" "$OPEN_PAYLOAD")" || {
  echo "FAIL: opening /session/start failed"
  exit 1
}
echo "$SESSION_RESP" | python3 -m json.tool
SESSION_ID="$(echo "$SESSION_RESP" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("session_id",""))')"
if [[ -z "$SESSION_ID" ]]; then
  echo "FAIL: opening session_id missing"
  exit 1
fi
echo "Session ID: $SESSION_ID"

# 5) Natural conversation (docs)
print_header "5) Natural conversation"
DOC_PAYLOAD="{\"session_id\":\"$SESSION_ID\",\"customer_id\":\"$CUSTOMER\",\"customer_message\":\"Haan, Nashik wali property ke documents aa gaye hain\"}"
DOC_RESP="$(http_post_json "/session/converse" "$DOC_PAYLOAD")" || DOC_RESP='{"error":"timeout"}'
echo "$DOC_RESP" | python3 -m json.tool

# 6) Income revision detection
print_header "6) Income revision"
REV_PAYLOAD="{\"session_id\":\"$SESSION_ID\",\"customer_id\":\"$CUSTOMER\",\"customer_message\":\"Actually, meri income ab 62000 ho gayi hai, promotion mila\"}"
REV_RESP="$(http_post_json "/session/converse" "$REV_PAYLOAD")" || REV_RESP='{"error":"timeout"}'
echo "$REV_RESP" | python3 -m json.tool
REV_PASS="$(echo "$REV_RESP" | python3 -c 'import sys,json; d=json.load(sys.stdin); facts=d.get("facts_extracted",[]); ok=any((f.get("type")=="income" and "62000" in str(f.get("value"))) for f in facts if isinstance(f,dict)); print("PASS" if ok else "FAIL")' 2>/dev/null)"
echo "income revision extraction: $REV_PASS"

# 7) Loan amount follow-up
print_header "7) Loan amount follow-up"
LOAN_PAYLOAD="{\"session_id\":\"$SESSION_ID\",\"customer_id\":\"$CUSTOMER\",\"customer_message\":\"Toh loan kitna milega mujhe?\"}"
LOAN_RESP="$(http_post_json "/session/converse" "$LOAN_PAYLOAD")" || LOAN_RESP='{"error":"timeout"}'
echo "$LOAN_RESP" | python3 -m json.tool

# 8) Cross-session memory check
print_header "8) Cross-session memory"
END_RESP="$(http_post_json "/session/end" "{\"session_id\":\"$SESSION_ID\",\"transcript\":\"Income revised to 62000\"}")" || END_RESP='{"error":"timeout"}'
echo "$END_RESP" | python3 -m json.tool
sleep 2
NEW_RESP="$(http_post_json "/session/start" "{\"customer_id\":\"$CUSTOMER\",\"session_type\":\"home_loan_processing\",\"agent_id\":\"$AGENT\",\"consent_id\":\"consent_rajesh_003\"}")" || NEW_RESP='{}'
echo "$NEW_RESP" | python3 -m json.tool
LATEST_INCOME="$(echo "$NEW_RESP" | python3 -c 'import sys,json; d=json.load(sys.stdin); r=((d.get("briefing") or {}).get("deterministic_recall") or {}).get("latest_income") or {}; print(r.get("value",""))' 2>/dev/null)"
echo "Latest recalled income: ${LATEST_INCOME:-<none>}"

# 9) PII tokenization check
print_header "9) PII tokenization"
NEW_SESSION_ID="$(echo "$NEW_RESP" | python3 -c 'import sys,json; print((json.load(sys.stdin).get("session_id") or ""))' 2>/dev/null)"
if [[ -n "$NEW_SESSION_ID" ]]; then
  PII_PAYLOAD="{\"session_id\":\"$NEW_SESSION_ID\",\"customer_id\":\"$CUSTOMER\",\"customer_message\":\"Mera PAN hai ABCDE1234F aur Aadhaar 987654321098\"}"
  PII_RESP="$(http_post_json "/session/converse" "$PII_PAYLOAD")" || PII_RESP='{"error":"timeout"}'
  echo "$PII_RESP" | python3 -m json.tool
fi
if grep -q "ABCDE1234F" "$WAL_PATH" 2>/dev/null; then
  echo "PII CHECK: FAIL (raw PAN found in WAL)"
else
  echo "PII CHECK: PASS (raw PAN not found in WAL)"
fi

# 10) Consent gate (strict check using empty consent)
print_header "10) Consent gate"
CONSENT_CODE=$(curl -sS --max-time 10 -o /dev/null -w "%{http_code}" -X POST "$BASE/session/start" \
  -H "Content-Type: application/json" \
  -d "{\"customer_id\":\"cust_no_consent_999\",\"session_type\":\"home_loan_processing\",\"agent_id\":\"$AGENT\",\"consent_id\":\"\"}")
echo "HTTP status: $CONSENT_CODE"
if [[ "$CONSENT_CODE" == "403" || "$CONSENT_CODE" == "400" ]]; then
  echo "Consent gate: PASS"
else
  echo "Consent gate: FAIL (expected 400/403)"
fi

AFTER_SNAPSHOT="$(snapshot_storage AFTER)"
echo ""
render_snapshot "Storage AFTER:" "$AFTER_SNAPSHOT"

IFS='|' read -r _ BW BM BC BR <<< "$BEFORE_SNAPSHOT"
IFS='|' read -r _ AW AM AC AR <<< "$AFTER_SNAPSHOT"

echo ""
echo "Storage DELTA:"
echo "  WAL entries:        $((AW-BW))"
echo "  Mem0 history rows:  $((AM-BM))"
echo "  Chroma embeddings:  $((AC-BC))"
echo "  Redis key count:    $((AR-BR))"

echo ""
print_header "ALL TESTS COMPLETE"
echo "Critical checks to report:"
echo "1) Opening/greeting continuity"
echo "2) Income revision extraction"
echo "3) Cross-session recall"
echo "4) WAL and storage deltas"
