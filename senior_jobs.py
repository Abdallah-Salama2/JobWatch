import httpx
import sqlite3
import os
import re
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent
DB_PATH = BASE / "jobs.db"

RAPIDAPI_KEY = os.environ["RAPIDAPI_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

MIN_YEARS = 3
MIN_STACK_MATCHES = 1

SENIOR_TITLES = ["senior", "sr", "lead", "principal", "staff", "architect", "head", "manager"]

QUERIES = [
    "Senior Full Stack Engineer in Egypt",
    "Senior Software Engineer in Cairo",
    "Senior PHP Laravel Developer in Egypt",
    "Senior Backend Developer in Cairo",
    "Senior Frontend React Developer in Egypt",
    "Lead Developer in Egypt"
]

STACK_KEYWORDS = [
    "php", "laravel", "javascript", "js", "typescript", "react", "vue",
    "inertia", "python", "node", "express", "sql", "mysql", "postgresql",
    "tailwind", "rest api", "restful", "docker", "git", "linux", "html", "css"
]

EXP_PATTERN = re.compile(
    r'(\d+)\s*(?:\+|–|-|to)?\s*(?:\d+\s*)?years?\s*(?:of\s*)?(?:experience|exp)',
    re.IGNORECASE
)

def is_stack_match(job):
    description = (job.get("description") or "").lower()
    if not description:
        return True
    matches = sum(1 for kw in STACK_KEYWORDS if kw in description)
    return matches >= MIN_STACK_MATCHES

def is_senior_experience(job):
    description = job.get("description") or ""
    matches = EXP_PATTERN.findall(description)
    if matches:
        return max(int(y) for y in matches) >= MIN_YEARS
    title = (job.get("job_title") or "").lower()
    return any(kw in title for kw in SENIOR_TITLES)

def fetch_jsearch_jobs():
    headers = {
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com"
    }
    
    results = []
    for q in QUERIES:
        try:
            r = httpx.get(
                "https://jsearch.p.rapidapi.com/search",
                headers=headers,
                params={
                    "query": q,
                    "page": "1",
                    "num_pages": "1"
                },
                timeout=15
            )
            r.raise_for_status()
            for job in r.json().get("data", []):
                results.append({
                    "id": f"js_{job.get('job_id')}",
                    "job_title": job.get("job_title"),
                    "company": job.get("employer_name") or "Unknown",
                    "description": job.get("job_description") or "",
                    "remote": job.get("job_is_remote", False),
                    "location": f"{job.get('job_city') or 'Egypt'}, EG" if job.get("job_city") else "Egypt",
                    "url": job.get("job_apply_link") or job.get("job_google_link") or ""
                })
        except Exception as e:
            print(f"JSearch error for query '{q}': {e}")
    return results

def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_jobs (
            id TEXT PRIMARY KEY,
            title TEXT,
            company TEXT,
            seen_at TEXT
        )
    """)
    conn.commit()

def send_telegram(msg):
    httpx.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        },
        timeout=10
    )

def main():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    jobs = fetch_jsearch_jobs()
    unique_jobs = {j["id"]: j for j in jobs}

    new_jobs = []
    for job in unique_jobs.values():
        if not is_senior_experience(job) or not is_stack_match(job):
            continue

        exists = conn.execute("SELECT 1 FROM seen_jobs WHERE id = ?", (job["id"],)).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO seen_jobs VALUES (?, ?, ?, ?)",
                (job["id"], job["job_title"], job["company"], datetime.now(timezone.utc).isoformat())
            )
            new_jobs.append(job)

    conn.commit()
    conn.close()

    if not new_jobs:
        print("No new senior jobs found via JSearch.")
        send_telegram("📭 No new matching Senior Egyptian jobs from JSearch today.")
        return

    send_telegram(f"👔 *{len(new_jobs)} Senior Egyptian job(s) found via JSearch:*")

    for job in new_jobs:
        modality = "🌍 Remote" if job["remote"] else "🏢 On-site / Hybrid"
        msg = (
            f"*{job['job_title']}* @ {job['company']}\n"
            f"📍 {job['location']} | {modality}\n"
            f"🔗 {job['url']}"
        )
        send_telegram(msg)

    print(f"Sent {len(new_jobs)} jobs.")

if __name__ == "__main__":
    main()
