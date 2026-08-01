import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import httpx

BASE = Path(__file__).parent
DB_PATH = BASE / "jobs.db"

# Fetch environment variables set in GitHub Actions Secrets
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

MAX_YEARS = 2
MIN_STACK_MATCHES = 1

# Egyptian localized searches for JSearch API
QUERIES = [
    "Full Stack Developer in Egypt",
    "Software Engineer in Cairo",
    "PHP Laravel Developer in Egypt",
    "Frontend React Vue Developer in Egypt",
    "Backend Node Python Engineer in Cairo",
    "Web Developer in Alexandria Egypt",
]

STACK_KEYWORDS = [
    "php",
    "laravel",
    "javascript",
    "js",
    "typescript",
    "react",
    "vue",
    "inertia",
    "python",
    "node",
    "express",
    "sql",
    "mysql",
    "postgresql",
    "tailwind",
    "rest api",
    "restful",
    "docker",
    "git",
    "linux",
    "html",
    "css",
]

EXP_PATTERN = re.compile(
    r"(\d+)\s*(?:\+|–|-|to)?\s*(?:\d+\s*)?years?\s*(?:of\s*)?(?:experience|exp)",
    re.IGNORECASE,
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
    if not RAPIDAPI_KEY:
        print("Error: RAPIDAPI_KEY is missing from environment variables.")
        return []

    headers = {
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
    }

    results = []
    for q in QUERIES:
        try:
            r = httpx.get(
                "https://jsearch.p.rapidapi.com/search",
                headers=headers,
                params={"query": q, "page": "1", "num_pages": "1"},
                timeout=15,
            )
            r.raise_for_status()
            for job in r.json().get("data", []):
                title = job.get("job_title", "").lower()
                # Exclude senior roles
                if any(
                    sr in title
                    for sr in [
                        "senior",
                        "lead",
                        "principal",
                        "manager",
                        "head",
                        "architect",
                    ]
                ):
                    continue

                results.append(
                    {
                        "id": f"js_{job.get('job_id')}",
                        "job_title": job.get("job_title"),
                        "company": job.get("employer_name") or "Unknown",
                        "description": job.get("job_description") or "",
                        "remote": job.get("job_is_remote", False),
                        "location": (
                            f"{job.get('job_city') or 'Egypt'}, EG"
                            if job.get("job_city")
                            else "Egypt"
                        ),
                        "url": job.get("job_apply_link")
                        or job.get("job_google_link")
                        or "",
                    }
                )
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
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram configuration missing.")
        return

    httpx.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        },
        timeout=10,
    )


def main():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    jobs = fetch_jsearch_jobs()

    # Deduplicate by unique job ID
    unique_jobs = {j["id"]: j for j in jobs}

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

        exists = conn.execute(
            "SELECT 1 FROM seen_jobs WHERE id = ?", (job["id"],)
        ).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO seen_jobs VALUES (?, ?, ?, ?)",
                (
                    job["id"],
                    job["job_title"],
                    job["company"],
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            new_jobs.append(job)

    conn.commit()
    conn.close()

    print(
        f"Filtered: {skipped_exp} over-exp, {skipped_stack} wrong stack, {len(new_jobs)} new sent."
    )

    if not new_jobs:
        print("No new jobs today.")
        send_telegram(
            "📭 No new matching Egyptian Junior/Mid jobs from JSearch today."
        )
        return

    send_telegram(
        f"🔍 *{len(new_jobs)} Junior/Mid Egyptian job(s) found via JSearch:* "
    )

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
