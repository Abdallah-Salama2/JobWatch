import httpx
import sqlite3
import os
import re
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent
DB_PATH = BASE / "jobs.db"

RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

MAX_YEARS = 3
MIN_STACK_MATCHES = 1

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


def exceeds_experience(job):
    description = job.get("description") or ""
    matches = EXP_PATTERN.findall(description)
    if not matches:
        return False
    return min(int(y) for y in matches) > MAX_YEARS


def fetch_jsearch_jobs():
    # This is where we stop and check: is the key even loaded?
    if not RAPIDAPI_KEY:
        print("STOP: RAPIDAPI_KEY is empty/not set in the environment. "
              "This is the #1 reason JSearch silently returns nothing.")
        return []

    print(f"Using RapidAPI key ending in: ...{RAPIDAPI_KEY[-6:]}")

    queries = [
        "Software Engineer in Egypt",
        "Full Stack Developer in Egypt",
        "PHP Laravel Developer in Egypt",
        "Frontend React Developer in Egypt"
    ]

    headers = {
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com"
    }

    results = []
    for q in queries:
        print(f"\n--- Querying: '{q}' ---")
        try:
            r = httpx.get(
                "https://jsearch.p.rapidapi.com/search-v2",
                headers=headers,
                params={"query": q, "num_pages": "1", "date_posted": "today"},
                timeout=15
            )
            print(f"Status code: {r.status_code}")

            # This is the key change: we look at the response BEFORE raising,
            # so a failure never disappears silently.
            if r.status_code != 200:
                print(f"Response body (first 500 chars): {r.text[:500]}")
                continue

            data = r.json()
            jobs_on_page = data.get("data", [])
            print(f"Jobs returned by API for this query: {len(jobs_on_page)}")

            for job in jobs_on_page:
                title = job.get("job_title", "").lower()
                if any(sr in title for sr in ["senior", "lead", "principal", "manager", "head"]):
                    continue

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

        except httpx.HTTPStatusError as e:
            print(f"HTTP error: {e.response.status_code} - {e.response.text[:500]}")
        except Exception as e:
            print(f"Unexpected error: {type(e).__name__}: {e}")

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
    r = httpx.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        },
        timeout=10
    )
    if r.status_code != 200:
        print(f"Telegram send failed: {r.status_code} - {r.text[:300]}")


def main():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    all_jobs = fetch_jsearch_jobs()
    print(f"\nTotal jobs fetched from RapidAPI (all queries): {len(all_jobs)}")

    unique_jobs = {}
    for j in all_jobs:
        if j["id"] not in unique_jobs:
            unique_jobs[j["id"]] = j

    new_jobs = []
    skipped_exp = 0
    skipped_stack = 0

    for job in unique_jobs.values():
        if exceeds_experience(job):
            skipped_exp += 1
            continue
        if not is_stack_match(job):
            skipped_stack += 1
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

    print(f"Filtered: {skipped_exp} over-exp, {skipped_stack} wrong stack, {len(new_jobs)} new.")

    if not new_jobs:
        print("No new jobs today.")
        send_telegram("📭 No new matching developer jobs today (RapidAPI only test).")
        return

    send_telegram(f"🔍 *{len(new_jobs)} dev job(s) matching your profile today (RapidAPI only test):*")

    for job in new_jobs:
        modality = "🌍 Remote" if job["remote"] else "🏢 On-site / Egypt"
        msg = (
            f"*{job['job_title']}* @ {job['company']}\n"
            f"📍 {job['location']} | {modality}\n"
            f"🔗 {job['url']}"
        )
        send_telegram(msg)

    print(f"Sent {len(new_jobs)} jobs.")


if __name__ == "__main__":
    main()
