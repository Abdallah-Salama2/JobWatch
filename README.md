
# JobWatch 🔍

A self-hosted Telegram bot that runs every morning and sends you remote backend, DevOps, and sysadmin job listings that match your stack — filtered by seniority, experience, and tech keywords.

Built on top of [JobsPipe](https://jobspipe.dev), runs as a systemd timer on a Linux server.

---

## How It Works

1. Every day at 7:00 AM, a systemd timer triggers the script

2. The script makes two API calls to JobsPipe:

   - One for **globally remote** jobs

   - One for **Egypt on-site** jobs

3. Results are deduplicated across both calls

4. Each job is filtered client-side:

   - Skips senior/lead/principal titles

   - Skips jobs requiring more than 2 years of experience

   - Skips jobs with no stack overlap (requires ≥2 keyword matches)

5. New jobs (not seen before) are stored in a local SQLite database

6. Each new job is sent as a Telegram message

---

## Prerequisites

- A Linux server or homelab running Debian/Ubuntu (with systemd)

- Python 3.10+

- A [JobsPipe](https://jobspipe.dev) account (free tier: 100 credits/month)

- A Telegram account

---

## Step 1 — Get a JobsPipe API Key

1. Sign up at [jobspipe.dev](https://jobspipe.dev)

2. Go to your dashboard → API Keys

3. Copy your `jp_live_...` key

---

## Step 2 — Create a Telegram Bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather)

2. Send `/newbot` and follow the prompts

3. Copy the bot token (format: `123456:ABC-xxxx`)

4. Message [@userinfobot](https://t.me/userinfobot) to get your personal chat ID (a number)

---

## Step 3 — Clone and Set Up

```bash

git clone https://github.com/alalfymansour/JobWatch.git

cd JobWatch

python3 -m venv venv

source venv/bin/activate

pip install httpx

```

---

## Step 4 — Configure Environment Variables

Create a `.env` file in the project root (never commit this):

```env

JOBSPIPE_KEY=jp_live_your_key_here

TELEGRAM_TOKEN=123456:ABC-your_token_here

TELEGRAM_CHAT_ID=your_chat_id_here

```

Secure it:

```bash

chmod 600 .env

```

---

## Step 5 — Test Manually

```bash

set -a && source .env && set +a

python main.py

```

You should see output like:

```

Filtered: 3 over-exp, 5 wrong stack, 12 new sent.

```

And receive Telegram messages for each matched job.

---

## Step 6 — Deploy as a systemd Timer

This runs the script automatically every day at 7:00 AM.

**Create the service file** at `/etc/systemd/system/jobwatch.service`:

```ini

[Unit]

Description=JobWatch daily job search

After=network-online.target

Wants=network-online.target

[Service]

Type=oneshot

User=your_linux_username

WorkingDirectory=/path/to/JobWatch

EnvironmentFile=/path/to/JobWatch/.env

ExecStart=/path/to/JobWatch/venv/bin/python /path/to/JobWatch/main.py

StandardOutput=journal

StandardError=journal

```

**Create the timer file** at `/etc/systemd/system/jobwatch.timer`:

```ini

[Unit]

Description=Run JobWatch every day at 7am

[Timer]

OnCalendar=*-*-* 04:00:00 UTC

Persistent=true

[Install]

WantedBy=timers.target

```

> `04:00 UTC` = `07:00 Cairo (EEST, UTC+3)`. Adjust for your timezone.

**Enable and start:**

```bash

sudo systemctl daemon-reload

sudo systemctl enable --now jobwatch.timer

```

**Verify next scheduled run:**

```bash

systemctl list-timers jobwatch.timer

```

---

## Customizing Filters

All filters are at the top of `main.py`:

| Variable | Default | Description |

|---|---|---|

| `MAX_YEARS` | `2` | Max years of experience required by the job |

| `MIN_STACK_MATCHES` | `2` | Minimum stack keyword matches in the description |

| `BASE_FILTERS["job_title_or"]` | See script | Job titles to search for |

| `BASE_FILTERS["job_title_not"]` | See script | Title keywords that disqualify a job |

| `BASE_FILTERS["job_seniority_or"]` | `entry, junior, mid` | Seniority levels from JobsPipe |

| `STACK_KEYWORDS` | See script | Tech keywords matched against job description |

---

## Viewing Logs

```bash

# After the timer runs

journalctl -u jobwatch.service -n 50

# Check next scheduled run

systemctl list-timers jobwatch.timer

```

---

## Project Structure

```

JobWatch/

├── main.py        # Main script

├── jobs.db        # SQLite database (auto-created, gitignored)

├── .env           # Secrets (gitignored)

├── venv/          # Python virtualenv (gitignored)

└── README.md

```

---

## Stack

- **Python** — core language

- **httpx** — HTTP client for API calls

- **SQLite** — local deduplication store

- **JobsPipe API** — job aggregation across 30+ ATS sources

- **Telegram Bot API** — delivery channel

- **systemd** — scheduling and process management

