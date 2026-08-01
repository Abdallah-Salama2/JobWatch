import httpx
import sqlite3
import os
import re
import time
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent
DB_PATH = BASE / "jobs.db"

JOBSPIPE_KEY = os.environ.get("JOBSPIPE_KEY")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

MIN_YEARS = 3

SENIOR_TITLES = ["senior", "sr", "lead", "principal", "staff", "architect", "head", "manager"]

# Stack filtering is intentionally disabled below (see is_stack_match).
# Kept here in case you want to turn it back on later.
STACK_KEYWORDS = [
    "php", "laravel", "javascript", "js", "typescript", "react", "vue",
    "inertia", "python", "node", "express", "sql", "mysql", "postgresql",
    "tailwind", "rest api", "restful", "docker", "git", "linux", "html", "css"
]

EXP_PATTERN = re.compile(
    r'(\d+)\s*(?:\+|–|-|to)?\s*(?:\d+\s*)?years?\s*(?:of\s*)?(?:experience|exp)',
    re.IGNORECASE
)

# ---------------------------------------------------------------------------
# JobsPipe (restored with the original senior filters)
# ---------------------------------------------------------------------------

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


def fetch_jobspipe_jobs():
    if not JOBSPIPE_KEY:
        print("JobsPipe: JOBSPIPE_KEY not set, skipping.")
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
            page_jobs = r.json().get("data", [])
            print(f"JobsPipe: {len(page_jobs)} jobs for search {'remote' if query.get('remote') else 'EG'}")
            for job in page_jobs:
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


# ---------------------------------------------------------------------------
# JSearch / RapidAPI (fixed for the current /search-v2 endpoint + response
# shape, now covering both Egypt and remote)
# ---------------------------------------------------------------------------

QUERIES = [
    "Senior Software Engineer",
    "Senior Full Stack Developer",
    "Senior Backend Developer",
    "Senior Frontend Developer",
]

# Each entry describes one "pass": text appended to the query, plus extra
# request params that tell JSearch how to scope the search.
JSEARCH_SEARCH_MODES = [
    {"suffix": "in Egypt", "extra_params": {"country": "eg"}},
    {"suffix": "remote", "extra_params": {"work_from_home": "true"}},
]


def fetch_jsearch_jobs():
    if not RAPIDAPI_KEY:
        print("JSearch: RAPIDAPI_KEY not set, skipping.")
        return []

    headers = {
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com"
    }

    results = []
    for base_q in QUERIES:
        for mode in JSEARCH_SEARCH_MODES:
            q = f"{base_q} {mode['suffix']}"
            print(f"\n--- Querying: '{q}' ---")
            try:
                params = {"query": q, "num_pages": "1", "date_posted": "today"}
                params.update(mode["extra_params"])

                r = httpx.get(
                    "https://jsearch.p.rapidapi.com/search-v2",
                    headers=headers,
                    params=params,
                    timeout=15
                )
                print(f"Status code: {r.status_code}")

                if r.status_code != 200:
                    print(f"Response body (first 500 chars): {r.text[:500]}")
                    continue

                data = r.json()
                # /search-v2 nests jobs under data.jobs (not data directly)
                jobs_on_page = data.get("data", {}).get("jobs", [])
                print(f"Jobs returned: {len(jobs_on_page)}")

                for job in jobs_on_page:
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


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def is_stack_match(job):
    # Disabled on purpose: show everything related to the field, even if the
    # description doesn't explicitly mention your exact stack keywords.
    return True


def is_senior_experience(job):
    description = job.get("description") or ""
    matches = EXP_PATTERN.findall(description)
    if matches:
        return max(int(y) for y in matches) >= MIN_YEARS
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


def send_telegram(msg, max_retries=5):
    for attempt in range(max_retries):
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
        if r.status_code == 200:
            return
        if r.status_code == 429:
            retry_after = r.json().get("parameters", {}).get("retry_after", 5)
            print(f"Telegram rate-limited, waiting {retry_after}s (attempt {attempt + 1}/{max_retries})")
            time.sleep(retry_after + 1)
            continue
        print(f"Telegram send failed: {r.status_code} - {r.text[:300]}")
        return
    print("Telegram send gave up after repeated 429s.")


def main():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    all_jobs = fetch_jobspipe_jobs() + fetch_jsearch_jobs()
    print(f"\nTotal jobs fetched (JobsPipe + JSearch, before dedup): {len(all_jobs)}")

    unique_jobs = {}
    for j in all_jobs:
        if j["id"] not in unique_jobs:
            unique_jobs[j["id"]] = j

    new_jobs = []
    for job in unique_jobs.values():
        if not is_senior_experience(job):
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

    print(f"Total new senior jobs: {len(new_jobs)}")

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
        time.sleep(1.1)  # stay under Telegram's ~1 msg/sec per-chat limit

    print(f"Sent {len(new_jobs)} jobs.")


if __name__ == "__main__":
    main()
