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

MAX_YEARS = 2
MIN_STACK_MATCHES = 2

BASE_FILTERS = {
    "job_title_or": [
        "backend engineer", "backend developer", "software engineer",
        "python developer", "python engineer", "flask developer",
        "api developer", "api engineer",
        "devops engineer", "devops developer",
        "site reliability engineer", "sre",
        "platform engineer", "infrastructure engineer",
        "sysadmin", "system administrator", "linux administrator",
        "linux engineer",
    ],
    "job_title_not": [
        "senior", "sr.", "lead", "principal", "staff",
        "head of", "manager", "director", "vp", "architect",
        "engineer iii", "engineer iv", "l4", "l5",
    ],
    "job_seniority_or": ["entry", "junior", "mid"],
    "posted_at_max_age_days": 1,
    "limit": 50
}

SEARCHES = [
    {**BASE_FILTERS, "remote": True},
    {**BASE_FILTERS, "job_country_code_or": ["EG"]},
]

STACK_KEYWORDS = [
    "python", "flask", "fastapi", "django",
    "go", "golang",
    "postgresql", "postgres",
    "docker", "kubernetes", "k8s",
    "linux", "debian", "ubuntu",
    "ansible", "terraform",
    "nginx", "traefik",
    "redis",
    "github actions", "ci/cd", "cicd",
    "bash", "shell script",
]

EXP_PATTERN = re.compile(
    r'(\d+)\s*(?:\+|–|-|to)?\s*(?:\d+\s*)?years?\s*(?:of\s*)?(?:experience|exp)',
    re.IGNORECASE
)

def is_stack_match(job):
    description = (job.get("description") or "").lower()
    if not description:
        return True  # no description → keep, safer than missing legit jobs
    matches = sum(1 for kw in STACK_KEYWORDS if kw in description)
    return matches >= MIN_STACK_MATCHES

def exceeds_experience(job):
    description = job.get("description") or ""
    matches = EXP_PATTERN.findall(description)
    if not matches:
        return False
    return min(int(y) for y in matches) > MAX_YEARS

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
        send_telegram(f"⚠️ JobWatch fetch failed: {e}")
        return

    new_jobs = []
    skipped_exp = 0
    skipped_stack = 0

    for job in jobs:
        job_id = str(job["id"])

        if exceeds_experience(job):
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

    print(f"Filtered: {skipped_exp} over-exp, {skipped_stack} wrong stack, {len(new_jobs)} new sent.")

    if not new_jobs:
        print("No new jobs today.")
        send_telegram("📭 No new matching jobs today.")
        return

    send_telegram(f"🔍 *{len(new_jobs)} job(s) matching your stack today:*")

    for job in new_jobs:
        sal = ""
        if job.get("min_annual_salary"):
            currency = job.get("salary_currency") or "USD"
            sal = f"\n💰 {job['min_annual_salary']:,.0f}–{job.get('max_annual_salary', '?'):,.0f} {currency}/yr"

        is_remote = job.get("remote")
        cities = job.get("cities") or []
        country = job.get("country") or ""
        location = cities[0] if cities else country or "Remote"
        modality = "🌍 Remote" if is_remote else "🏢 On-site"

        apply_url = job.get("url") or job.get("final_url") or job.get("source_url") or ""

        msg = (
            f"*{job.get('job_title')}* @ {job.get('company') or 'Unknown'}\n"
            f"📍 {location} | {modality}"
            f"{sal}\n"
            f"🔗 {apply_url}"
        )
        send_telegram(msg)

    print(f"Sent {len(new_jobs)} jobs.")

if __name__ == "__main__":
    main()
