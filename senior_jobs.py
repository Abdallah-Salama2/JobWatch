import httpx
import sqlite3
import os
import re
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent
DB_PATH = BASE / "jobs.db"

JOBSPIPE_KEY = os.environ.get("JOBSPIPE_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

MIN_YEARS = 3
MIN_STACK_MATCHES = 1

SENIOR_TITLES = ["senior", "sr", "lead", "principal", "staff", "architect", "head", "manager"]

BASE_FILTERS = {
    "job_title_or": [
        "senior fullstack developer", "senior software engineer", "lead software engineer",
        "senior backend engineer", "senior php developer", "senior laravel developer",
        "senior python developer", "senior node developer", "senior frontend developer", "senior react developer"
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
    if matches:
        return max(int(y) for y in matches) >= MIN_YEARS
    title = (job.get("job_title") or "").lower()
    return any(kw in title for kw in SENIOR_TITLES)

def fetch_jobspipe_jobs():
    if not JOBSPIPE_KEY:
        return []
    results = []
    for query in SEARCHES:
        try:
            r = httpx.post(
                "https://api.jobspipe.dev/v1/jobs/search",
                headers={"Authorization": f"Bearer {JOBSPIPE_KEY}"},
                json=query,
                timeout=15
            )
            r.raise_for_status()
            for job in r.json().get("data", []):
                results.append({
                    "id": f"jp_{job['id']}",
                    "job_title": job.get("job_title"),
                    "company": job.get("company") or "Unknown",
                    "description": job.get("description") or "",
                    "remote": job.get("remote"),
                    "location": (job.get("cities") or [job.get("country") or "Egypt"])[0],
                    "url": job.get("url") or job.get("final_url") or "",
                    "salary": f"{job['min_annual_salary']:,.0f} USD/yr" if job.get("min_annual_salary") else ""
                })
        except Exception as e:
            print(f"JobsPipe fetch warning: {e}")
    return results

def fetch_jsearch_jobs():
    if not RAPIDAPI_KEY:
        return []
    
    queries = [
        "Senior Software Engineer in Egypt",
        "Senior Full Stack Developer in Egypt",
        "Senior Backend Developer in Egypt",
        "Senior Frontend Developer in Egypt"
    ]
    
    headers = {
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com"
    }
    
    results = []
    for q in queries:
        try:
            r = httpx.get(
                "https://jsearch.p.rapidapi.com/search",
                headers=headers,
                params={"query": q, "page": "1", "num_pages": "1", "date_posted": "today"},
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
                    "url": job.get("job_apply_link") or "",
                    "salary": ""
                })
        except Exception as e:
            print(f"JSearch fetch warning: {e}")
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

    all_jobs = fetch_jobspipe_jobs() + fetch_jsearch_jobs()
    
    unique_jobs = {}
    for j in all_jobs:
        if j["id"] not in unique_jobs:
            unique_jobs[j["id"]] = j

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
        print("No new senior jobs today.")
        send_telegram("📭 No new matching Senior (3+ yrs) developer jobs today.")
        return

    send_telegram(f"👔 *{len(new_jobs)} Senior (3+ yrs) dev job(s) found today:*")

    for job in new_jobs:
        modality = "🌍 Remote" if job["remote"] else "🏢 On-site / Egypt"
        sal = f"\n💰 {job['salary']}" if job["salary"] else ""

        msg = (
            f"*{job['job_title']}* @ {job['company']}\n"
            f"📍 {job['location']} | {modality}"
            f"{sal}\n"
            f"🔗 {job['url']}"
        )
        send_telegram(msg)

if __name__ == "__main__":
    main()
