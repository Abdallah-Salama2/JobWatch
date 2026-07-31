import httpx
import sqlite3
import os
import re
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent
DB_PATH = BASE / "jobs.db"

JOBSPIPE_KEY = os.environ["JOBSPIPE_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

MIN_YEARS = 3
MIN_STACK_MATCHES = 1

SENIOR_TITLES = [
    "senior", "sr", "lead", "principal", "staff", "architect", "head", "manager"
]

BASE_FILTERS = {
    "job_title_or": [
        # Senior Full-Stack & Software Roles
        "senior fullstack developer", "senior fullstack engineer", "senior software engineer",
        "lead software engineer", "principal software engineer", "software architect",
        
        # Senior Backend & APIs
        "senior backend engineer", "senior backend developer", "senior php developer",
        "senior laravel developer", "senior python developer", "senior node developer",
        
        # Senior Frontend
        "senior frontend developer", "senior frontend engineer", "senior react developer", 
        "senior vue developer", "senior javascript developer"
    ],
    "job_seniority_or": ["senior", "lead", "expert"],
    "posted_at_max_age_days": 1,
    "limit": 50
}

SEARCHES = [
    {**BASE_FILTERS, "remote": True},
    {**BASE_FILTERS, "job_country_code_or": ["EG"]},
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
    
    # If explicit years are found in the description
    if matches:
        return max(int(y) for y in matches) >= MIN_YEARS
        
    # Fallback: check if job title explicitly mentions a senior keyword
    title = (job.get("job_title") or "").lower()
    return any(kw in title for kw in SENIOR_TITLES)

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

def fetch_jobs():
    seen_ids = {}
    results = []
    for query in SEARCHES:
        r = httpx.post(
            "https://api.jobspipe.dev/v1/jobs/search",
            headers={"Authorization": f"Bearer {JOBSPIPE_KEY}"},
            json=query,
            timeout=15
        )
        r.raise_for_status()
        for job in r.json().get("data", []):
            job_id = str(job["id"])
            if job_id not in seen_ids:
                seen_ids[job_id] = True
                results.append(job)
    return results

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

    try:
        jobs = fetch_jobs()
    except Exception as e:
        send_telegram(f"⚠️ Senior JobWatch fetch failed: {e}")
        return

    new_jobs = []
    skipped_exp = 0
    skipped_stack = 0

    for job in jobs:
        job_id = str(job["id"])

        if not is_senior_experience(job):
            skipped_exp += 1
            continue

        if not is_stack_match(job):
            skipped_stack += 1
            continue

        exists = conn.execute(
            "SELECT 1 FROM seen_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO seen_jobs VALUES (?, ?, ?, ?)",
                (job_id, job.get("job_title"), job.get("company"),
                 datetime.now(timezone.utc).isoformat())
            )
            new_jobs.append(job)

    conn.commit()
    conn.close()

    print(f"Filtered: {skipped_exp} under-exp, {skipped_stack} wrong stack, {len(new_jobs)} new sent.")

    if not new_jobs:
        print("No new senior jobs today.")
        send_telegram("📭 No new matching Senior (3+ yrs) developer jobs today.")
        return

    send_telegram(f"👔 *{len(new_jobs)} Senior (3+ yrs) dev job(s) found today:*")

    for job in new_jobs:
        sal = ""
        if job.get("min_annual_salary"):
            currency = job.get("salary_currency") or "USD"
            sal = f"\n💰 {job['min_annual_salary']:,.0f}–{job.get('max_annual_salary', '?'):,.0f} {currency}/yr"

        is_remote = job.get("remote")
        cities = job.get("cities") or []
        country = job.get("country") or ""
        location = cities[0] if cities else country or "Remote"
        modality = "🌍 Remote" if is_remote else "🏢 On-site / Egypt"

        apply_url = job.get("url") or job.get("final_url") or job.get("source_url") or ""

        msg = (
            f"*{job.get('job_title')}* @ {job.get('company') or 'Unknown'}\n"
            f"📍 {location} | {modality}"
            f"{sal}\n"
            f"🔗 {apply_url}"
        )
        send_telegram(msg)

    print(f"Sent {len(new_jobs)} senior jobs.")

if __name__ == "__main__":
    main()
