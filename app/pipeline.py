import re
import csv
import json
import time
import logging
import statistics
from datetime import datetime
from typing import List, Dict, Any, Tuple
import httpx
from .config import settings

logger = logging.getLogger("pipeline")
logging.basicConfig(level=logging.INFO)

# ─────────────────────────────────────────────────────────────
# Rule-based fallback classifier (no API key required)
# ─────────────────────────────────────────────────────────────
def rule_based_classify(merchant: str) -> str:
    m = merchant.lower().strip()
    if any(k in m for k in ("swiggy", "zomato", "restaurant", "food", "cafe", "starbucks", "mcdonalds", "kfc")):
        return "Food"
    if any(k in m for k in ("amazon", "flipkart", "myntra", "meesho", "shopping", "mart", "ebay", "walmart")):
        return "Shopping"
    if any(k in m for k in ("irctc", "makemytrip", "travel", "flight", "train", "booking", "expedia")):
        return "Travel"
    if any(k in m for k in ("uber", "ola", "taxi", "cab", "metro")):
        return "Transport"
    if any(k in m for k in ("recharge", "jio", "airtel", "electricity", "water", "bill", "utilities", "gas")):
        return "Utilities"
    if any(k in m for k in ("atm", "hdfc atm", "cash", "withdrawal", "sbi atm", "icici atm")):
        return "Cash Withdrawal"
    if any(k in m for k in ("netflix", "spotify", "cinema", "movies", "entertainment", "bookmyshow", "hotstar", "prime video")):
        return "Entertainment"
    return "Other"


# ─────────────────────────────────────────────────────────────
# Date / Amount parsers
# ─────────────────────────────────────────────────────────────
def parse_date(date_str: str) -> Any:
    if not date_str:
        return None
    date_str = date_str.strip()
    formats = ("%d-%m-%Y", "%Y/%m/%d", "%Y-%m-%d", "%d/%m/%Y")
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


def parse_amount(amount_str: str) -> float:
    if not amount_str:
        return 0.0
    cleaned = re.sub(r'[^\d.-]', '', amount_str.strip())
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


# ─────────────────────────────────────────────────────────────
# Groq API caller (OpenAI-compatible, JSON mode)
# ─────────────────────────────────────────────────────────────
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL   = "llama-3.3-70b-versatile"

def call_groq_with_retry(prompt: str, api_key: str, max_retries: int = 3) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    data = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }

    backoff = 1.0
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(GROQ_API_URL, json=data, headers=headers)
                response.raise_for_status()
                res_json = response.json()
                text = res_json["choices"][0]["message"]["content"]
                return text
        except Exception as e:
            last_error = e
            logger.warning(f"Groq API call attempt {attempt + 1} failed: {e}")
            if attempt < max_retries:
                time.sleep(backoff)
                backoff *= 2

    raise last_error


# ─────────────────────────────────────────────────────────────
# Step A: Data Cleaning
# ─────────────────────────────────────────────────────────────
def clean_and_parse_csv_data(file_content: str) -> Tuple[List[Dict[str, Any]], int]:
    reader = csv.DictReader(file_content.splitlines())
    raw_rows = list(reader)
    raw_count = len(raw_rows)

    cleaned_rows = []
    seen_keys = set()

    for row in raw_rows:
        txn_id    = row.get("txn_id", "").strip() or None
        date_val  = parse_date(row.get("date", ""))
        merchant  = row.get("merchant", "").strip()
        amount_val = parse_amount(row.get("amount", ""))

        currency_val = row.get("currency", "").strip().upper()
        if currency_val not in ("INR", "USD"):
            currency_val = currency_val or "INR"

        status_val   = row.get("status", "").strip().upper()
        category_val = row.get("category", "").strip() or "Uncategorised"
        account_id   = row.get("account_id", "").strip()
        notes        = row.get("notes", "").strip() or None

        date_str = date_val.isoformat() if date_val else ""
        dup_key  = (txn_id, date_str, merchant, amount_val, currency_val,
                    status_val, category_val, account_id, notes)

        if dup_key in seen_keys:
            continue
        seen_keys.add(dup_key)

        cleaned_rows.append({
            "txn_id": txn_id,
            "date": date_val,
            "merchant": merchant,
            "amount": amount_val,
            "currency": currency_val,
            "status": status_val,
            "category": category_val,
            "account_id": account_id,
            "notes": notes,
            "is_anomaly": False,
            "anomaly_reason": None,
            "llm_category": None,
            "llm_raw_response": None,
            "llm_failed": False,
        })

    return cleaned_rows, raw_count


# ─────────────────────────────────────────────────────────────
# Step B: Anomaly Detection
# ─────────────────────────────────────────────────────────────
DOMESTIC_ONLY_MERCHANTS = {"swiggy", "ola", "irctc"}

def process_anomaly_detection(transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Build per-account amount lists
    account_amounts: Dict[str, List[float]] = {}
    for txn in transactions:
        acc = txn["account_id"]
        account_amounts.setdefault(acc, []).append(txn["amount"])

    account_medians = {acc: statistics.median(amts) for acc, amts in account_amounts.items() if amts}

    for txn in transactions:
        reasons = []

        median = account_medians.get(txn["account_id"], 0.0)
        if median > 0 and txn["amount"] > 3 * median:
            reasons.append(
                f"Outlier: Amount exceeds 3x median of account "
                f"{txn['account_id']} (median: {median:.2f})"
            )

        if txn["currency"] == "USD" and txn["merchant"].lower().strip() in DOMESTIC_ONLY_MERCHANTS:
            reasons.append(
                f"Currency mismatch: {txn['merchant']} is domestic-only but currency is USD"
            )

        if reasons:
            txn["is_anomaly"] = True
            txn["anomaly_reason"] = " | ".join(reasons)

    return transactions


# ─────────────────────────────────────────────────────────────
# Step C: LLM Classification (Groq → fallback)
# ─────────────────────────────────────────────────────────────
ALLOWED_CATEGORIES = {
    "Food", "Shopping", "Travel", "Transport",
    "Utilities", "Cash Withdrawal", "Entertainment", "Other"
}

def run_llm_classification(transactions: List[Dict[str, Any]], api_key: str) -> List[Dict[str, Any]]:
    uncategorized = [t for t in transactions if t["category"] == "Uncategorised"]
    if not uncategorized:
        return transactions

    if not api_key:
        logger.info("No GROQ_API_KEY found — using rule-based fallback classification.")
        for txn in uncategorized:
            cat = rule_based_classify(txn["merchant"])
            txn["category"] = cat
            txn["llm_category"] = cat
            txn["llm_raw_response"] = "Mock LLM: rule-based fallback"
        return transactions

    batch_size = 15
    for i in range(0, len(uncategorized), batch_size):
        batch = uncategorized[i : i + batch_size]
        input_data = [
            {"idx": idx, "merchant": t["merchant"], "amount": t["amount"],
             "currency": t["currency"], "notes": t["notes"] or ""}
            for idx, t in enumerate(batch)
        ]

        prompt = (
            "You are a financial transaction classifier. Classify each transaction into EXACTLY one of: "
            "Food, Shopping, Travel, Transport, Utilities, Cash Withdrawal, Entertainment, Other.\n\n"
            f"Transactions:\n{json.dumps(input_data, indent=2)}\n\n"
            "Return a JSON object with key \"results\" containing an array of objects with \"idx\" and \"category\".\n"
            "Example: {\"results\": [{\"idx\": 0, \"category\": \"Shopping\"}]}"
        )

        try:
            response_text = call_groq_with_retry(prompt, api_key)
            parsed = json.loads(response_text)
            classifications = parsed.get("results", parsed) if isinstance(parsed, dict) else parsed
            class_map = {
                item["idx"]: item["category"]
                for item in classifications
                if isinstance(item, dict) and "idx" in item and "category" in item
            }

            for idx, txn in enumerate(batch):
                assigned = class_map.get(idx)
                cat = assigned if assigned in ALLOWED_CATEGORIES else rule_based_classify(txn["merchant"])
                txn["category"]          = cat
                txn["llm_category"]      = cat
                txn["llm_raw_response"]  = response_text

        except Exception as e:
            logger.error(f"Groq classify batch failed: {e}. Falling back to rule-based.")
            for txn in batch:
                cat = rule_based_classify(txn["merchant"])
                txn["category"]          = cat
                txn["llm_category"]      = cat
                txn["llm_raw_response"]  = f"LLM error: {e}"
                txn["llm_failed"]        = True

    return transactions


# ─────────────────────────────────────────────────────────────
# Step D: LLM Narrative + Risk Summary (Groq → fallback)
# ─────────────────────────────────────────────────────────────
def generate_summary_and_narrative(transactions: List[Dict[str, Any]], api_key: str) -> Dict[str, Any]:
    total_spend_inr = round(sum(t["amount"] for t in transactions if t["currency"] == "INR"), 2)
    total_spend_usd = round(sum(t["amount"] for t in transactions if t["currency"] == "USD"), 2)

    merchant_spend: Dict[str, float] = {}
    for t in transactions:
        merchant_spend[t["merchant"]] = merchant_spend.get(t["merchant"], 0.0) + t["amount"]
    top_3 = [
        {"merchant": name, "total_spend": round(amt, 2)}
        for name, amt in sorted(merchant_spend.items(), key=lambda x: x[1], reverse=True)[:3]
    ]
    anomaly_count = sum(1 for t in transactions if t["is_anomaly"])

    summary: Dict[str, Any] = {
        "total_spend_inr": total_spend_inr,
        "total_spend_usd": total_spend_usd,
        "top_merchants": top_3,
        "anomaly_count": anomaly_count,
        "narrative": "",
        "risk_level": "low",
    }

    def rule_based_risk_and_narrative() -> None:
        summary["narrative"] = (
            f"The dataset shows a total spend of INR {total_spend_inr:,} and USD {total_spend_usd:,}. "
            f"Top merchants by volume: {', '.join(m['merchant'] for m in top_3)}. "
            f"{anomaly_count} transaction(s) were flagged as anomalies."
        )
        summary["risk_level"] = "low" if anomaly_count == 0 else ("medium" if anomaly_count <= 2 else "high")

    if not api_key:
        rule_based_risk_and_narrative()
        return summary

    sample = [
        {"merchant": t["merchant"], "amount": t["amount"], "currency": t["currency"],
         "category": t["category"], "is_anomaly": t["is_anomaly"]}
        for t in transactions[:20]
    ]
    prompt = (
        "You are a senior financial analyst summarising an uploaded transaction report.\n\n"
        f"Metrics:\n"
        f"- Total INR spend: {total_spend_inr}\n"
        f"- Total USD spend: {total_spend_usd}\n"
        f"- Anomaly count: {anomaly_count}\n"
        f"- Top 3 merchants: {json.dumps(top_3)}\n\n"
        f"Sample transactions:\n{json.dumps(sample, indent=2)}\n\n"
        "Return a JSON object with exactly two keys:\n"
        "  \"narrative\": a professional 2-3 sentence spending summary,\n"
        "  \"risk_level\": one of \"low\", \"medium\", or \"high\".\n"
        "Example: {\"narrative\": \"...\", \"risk_level\": \"medium\"}"
    )

    try:
        response_text = call_groq_with_retry(prompt, api_key)
        data = json.loads(response_text)
        summary["narrative"]  = data.get("narrative", "")
        risk = data.get("risk_level", "low").lower().strip()
        summary["risk_level"] = risk if risk in ("low", "medium", "high") else "medium"
    except Exception as e:
        logger.error(f"Groq narrative generation failed: {e}. Falling back to rule-based.")
        rule_based_risk_and_narrative()

    return summary
