# Daily LeetCode Auto-Submitter

Automatically fetches the LeetCode daily challenge, generates a Python3 solution via **Gemini 1.5 Flash** (free), submits it, and emails you the verdict — runs every day on GitHub's servers, no PC required.

---

## Architecture

```
GitHub Actions (cron 02:00 UTC)
        │
        ▼
solve_and_submit.py
  ├─ [1] LeetCode GraphQL  → fetch daily problem
  ├─ [2] Gemini 1.5 Flash  → generate Python3 solution
  ├─ [3] LeetCode /submit/ → post the code
  ├─ [4] Poll /check/      → wait for verdict (retry ×3 on failure)
  └─ [5] Gmail SMTP        → email you the result
```

---

## One-time Setup

### Step 1 — Create a private GitHub repo

1. Go to [github.com/new](https://github.com/new).
2. Name it anything (e.g. `lc-daily`), set it to **Private**, click **Create repository**.
3. Push this project folder to that repo:

```bash
git init
git remote add origin https://github.com/YOUR_USERNAME/lc-daily.git
git add .
git commit -m "init"
git push -u origin main
```

---

### Step 2 — Grab your LeetCode cookies

1. Open [leetcode.com](https://leetcode.com) in Chrome, log in.
2. Open DevTools → **Application** tab → **Cookies** → `https://leetcode.com`.
3. Copy the value of **`LEETCODE_SESSION`** (long token).
4. Copy the value of **`csrftoken`**.

> ⚠️ These expire every few weeks. When the workflow fails, refresh them here.

---

### Step 3 — Get a free Gemini API key

1. Go to [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey).
2. Click **Create API key** → copy it.

The free tier is 15 requests/min and 1500 requests/day — more than enough.

---

### Step 4 — Get a Gmail App Password

> Only works if your Gmail has **2FA enabled**.

1. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).
2. App name: `leetcode-bot` → click **Create**.
3. Copy the 16-character password.

---

### Step 5 — Add GitHub Secrets

In your GitHub repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.

Add all six secrets:

| Secret name | Value |
|---|---|
| `LEETCODE_SESSION` | The long cookie value from Step 2 |
| `CSRF_TOKEN` | The `csrftoken` cookie value from Step 2 |
| `GEMINI_API_KEY` | API key from Step 3 |
| `EMAIL_FROM` | Your Gmail address (`you@gmail.com`) |
| `EMAIL_PASSWORD` | The 16-char App Password from Step 4 |
| `EMAIL_TO` | Destination email (can be the same Gmail) |

---

### Step 6 — Enable GitHub Actions

1. In your repo → click the **Actions** tab.
2. If prompted, click **"I understand my workflows, go ahead and enable them"**.
3. To test immediately: click **Daily LeetCode Auto-Submit** → **Run workflow** → **Run workflow**.

---

## Schedule

The workflow runs at **02:00 UTC every day** (= **07:30 IST**).  
LeetCode resets the daily problem at 00:00 UTC, so this is safely after the reset.

To change the time, edit `.github/workflows/daily_leetcode.yml`:
```yaml
- cron: "0 2 * * *"   # minute hour * * *
```

---

## What you'll get in your inbox

```
Subject: [LeetCode Daily] ACCEPTED: Two Sum (Easy)

Daily LeetCode Auto-Solver Report
==================================
Date       : 2026-10-03
Problem    : Two Sum
Difficulty : Easy
Link       : https://leetcode.com/problems/two-sum/

Result
------
ACCEPTED
   Runtime : 42 ms
   Memory  : 16.4 MB
   Beat    : 89.3% in runtime

Submitted Code
--------------
class Solution:
    def twoSum(self, nums, target):
        seen = {}
        for i, n in enumerate(nums):
            if target - n in seen:
                return [seen[target - n], i]
            seen[n] = i
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `403 Forbidden` on submit | Refresh `LEETCODE_SESSION` and `CSRF_TOKEN` secrets |
| `Cloudflare challenge` / `429` | LeetCode is blocking GitHub IPs — wait a day or add a proxy |
| Gemini quota exceeded | Free tier: 1500 req/day. This uses 1-3 per day, so very unlikely |
| Email not received | Check spam; ensure App Password is correct and 2FA is on |
| Workflow not running | Check Actions tab is enabled; GitHub disables crons after 60 days of inactivity — push any commit to re-enable |

---

## Files

```
.
├── .github/
│   └── workflows/
│       └── daily_leetcode.yml   # GitHub Actions cron job
├── scripts/
│   └── solve_and_submit.py      # Main Python script
├── requirements.txt             # Python dependencies
└── README.md                    # This file
```
