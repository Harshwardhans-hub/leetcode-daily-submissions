"""
Daily LeetCode Auto-Solver & Submitter
--------------------------------------
Flow:
  1. Fetch today's daily challenge via LeetCode GraphQL
  2. Ask Gemini to generate a Python3 solution
  3. Submit via LeetCode's internal submit endpoint
  4. Poll for verdict; retry up to 3 times on wrong-answer/runtime-error
  5. Send an email summary
"""

import os
import sys
import time
import re
import smtplib
import textwrap
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests
from google import genai

# ── credentials from env (GitHub Secrets) ────────────────────────────────────
LEETCODE_SESSION = os.environ["LEETCODE_SESSION"]
CSRF_TOKEN       = os.environ["CSRF_TOKEN"]
GEMINI_API_KEY   = os.environ["GEMINI_API_KEY"]
EMAIL_FROM       = os.environ["EMAIL_FROM"]      # your Gmail address
EMAIL_PASSWORD   = os.environ["EMAIL_PASSWORD"]  # Gmail App Password
EMAIL_TO         = os.environ["EMAIL_TO"]        # recipient (can be same)

# ── constants ─────────────────────────────────────────────────────────────────
LC_GRAPHQL   = "https://leetcode.com/graphql"
LC_ORIGIN    = "https://leetcode.com"
MAX_RETRIES  = 3
POLL_DELAY   = 3   # seconds between submission status polls
POLL_TIMEOUT = 60  # max seconds to wait for a verdict

HEADERS = {
    "User-Agent":     "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer":        "https://leetcode.com",
    "x-csrftoken":    CSRF_TOKEN,
    "Cookie":         f"LEETCODE_SESSION={LEETCODE_SESSION}; csrftoken={CSRF_TOKEN}",
    "Content-Type":   "application/json",
    "Origin":         LC_ORIGIN,
}

# ── Gemini setup ──────────────────────────────────────────────────────────────
client = genai.Client(api_key=GEMINI_API_KEY)   # free-tier: gemini-2.0-flash


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 – Fetch today's daily challenge
# ─────────────────────────────────────────────────────────────────────────────
def fetch_daily_problem() -> dict:
    """Return a dict with keys: title, slug, difficulty, content, starter, question_id, link."""
    query = """
    query dailyChallenge {
      activeDailyCodingChallengeQuestion {
        date
        link
        question {
          questionId
          title
          titleSlug
          difficulty
          content
          codeSnippets {
            lang
            langSlug
            code
          }
        }
      }
    }
    """
    resp = requests.post(LC_GRAPHQL, json={"query": query}, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()["data"]["activeDailyCodingChallengeQuestion"]
    question = data["question"]

    starter = ""
    for snippet in question.get("codeSnippets", []):
        if snippet["langSlug"] == "python3":
            starter = snippet["code"]
            break

    return {
        "title":       question["title"],
        "slug":        question["titleSlug"],
        "difficulty":  question["difficulty"],
        "content":     question["content"],
        "starter":     starter,
        "question_id": question["questionId"],
        "link":        f"https://leetcode.com{data['link']}",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 – Generate solution with Gemini
# ─────────────────────────────────────────────────────────────────────────────
def strip_html(text: str) -> str:
    """Remove HTML tags for a cleaner prompt."""
    return re.sub(r"<[^>]+>", "", text).strip()


def ask_gemini(problem: dict, previous_error: str = "") -> str:
    """Ask Gemini to produce a Python3 solution. Returns only the code."""
    content_clean = strip_html(problem["content"])

    error_context = ""
    if previous_error:
        error_context = f"""
The previous submission failed with this error/output:
{previous_error}

Please analyse what went wrong and provide a corrected solution.
"""

    prompt = textwrap.dedent(f"""
        You are an expert competitive programmer.
        Solve the following LeetCode problem in Python3.

        Problem title: {problem['title']}
        Difficulty: {problem['difficulty']}

        Problem statement:
        {content_clean}

        Starter code (use EXACTLY this class/function signature):
        {problem['starter']}
        {error_context}
        Rules:
        - Output ONLY valid Python3 code, nothing else.
        - Do NOT include markdown fences, explanations, or comments.
        - The code must be a complete, self-contained Solution class (or function) that
          passes all LeetCode test cases.
        - Optimise for correctness first, then time complexity.
    """)

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
    )
    code = response.text.strip()

    # Strip accidental markdown fences Gemini sometimes adds
    code = re.sub(r"^```(?:python3?)?\n?", "", code)
    code = re.sub(r"\n?```$", "", code)
    return code.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 – Submit to LeetCode
# ─────────────────────────────────────────────────────────────────────────────
def submit_solution(problem: dict, code: str) -> str:
    """Submit code and return the submission id."""
    url = f"https://leetcode.com/problems/{problem['slug']}/submit/"
    payload = {
        "lang":        "python3",
        "question_id": problem["question_id"],
        "typed_code":  code,
    }
    resp = requests.post(url, json=payload, headers=HEADERS, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Submit HTTP {resp.status_code}: {resp.text[:400]}")
    return resp.json()["submission_id"]


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 – Poll for verdict
# ─────────────────────────────────────────────────────────────────────────────
def poll_verdict(submission_id: str) -> dict:
    """Poll until we get a final verdict. Returns the full result dict."""
    check_url = f"https://leetcode.com/submissions/detail/{submission_id}/check/"
    deadline  = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        resp = requests.get(check_url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        result = resp.json()
        state  = result.get("state", "")
        if state == "SUCCESS":
            return result
        print(f"  Polling... state={state}")
        time.sleep(POLL_DELAY)
    raise TimeoutError("Submission did not return a verdict within the timeout.")


def parse_verdict(result: dict) -> tuple:
    """
    Returns (accepted: bool, description: str).
    status_msg values: 'Accepted', 'Wrong Answer', 'Runtime Error',
                       'Time Limit Exceeded', 'Memory Limit Exceeded', etc.
    """
    msg = result.get("status_msg", "Unknown")
    accepted = (msg == "Accepted")

    if accepted:
        percentile = result.get("runtime_percentile", 0)
        desc = (
            f"ACCEPTED\n"
            f"   Runtime : {result.get('status_runtime', '?')}\n"
            f"   Memory  : {result.get('status_memory', '?')}\n"
            f"   Beat    : {percentile:.1f}% in runtime"
        )
    else:
        last_tc = result.get("last_testcase", "")
        got     = result.get("code_output", "")
        expect  = result.get("expected_output", "")
        err     = result.get("full_runtime_error", "") or result.get("compile_error", "")
        desc = (
            f"FAILED: {msg}\n"
            f"   Last test: {repr(last_tc)}\n"
            f"   Got      : {repr(got)}\n"
            f"   Expected : {repr(expect)}\n"
            f"   Error    : {err}"
        )
    return accepted, desc


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 – Send email
# ─────────────────────────────────────────────────────────────────────────────
def send_email(subject: str, body: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_FROM
    msg["To"]      = EMAIL_TO
    msg.attach(MIMEText(body, "plain"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_FROM, EMAIL_PASSWORD)
        server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
    print("Email sent.")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    print("=" * 60)
    print("LeetCode Daily Auto-Solver")
    print("=" * 60)

    # 1. Fetch daily problem
    print("\n[1/4] Fetching today's daily problem...")
    problem = fetch_daily_problem()
    print(f"  -> {problem['title']} ({problem['difficulty']}) -- {problem['link']}")

    # 2-4. Generate -> Submit -> Verify, with retry loop
    print("\n[2/4] Generating solution...")
    accepted     = False
    final_desc   = "No submission made."
    final_code   = ""
    error_msg    = ""

    for attempt in range(1, MAX_RETRIES + 1):
        print(f"\n  Attempt {attempt}/{MAX_RETRIES}")
        try:
            code = ask_gemini(problem, previous_error=error_msg)
            print(f"  Generated {len(code)} chars of code.")

            print("[3/4] Submitting...")
            sub_id = submit_solution(problem, code)
            print(f"  Submission ID: {sub_id}")

            print("[4/4] Waiting for verdict...")
            result           = poll_verdict(str(sub_id))
            accepted, final_desc = parse_verdict(result)
            final_code       = code
            print(f"  Verdict: {final_desc}")

            if accepted:
                break

            # Build error context for next attempt
            error_msg = final_desc
            print("  Not accepted -- will retry with error context.")
            time.sleep(5)

        except Exception as exc:
            error_msg  = str(exc)
            final_desc = f"Error on attempt {attempt}: {exc}"
            print(f"  Exception: {exc}")
            time.sleep(5)

    # 5. Email report
    status_word = "ACCEPTED" if accepted else "FAILED"
    subject = f"[LeetCode Daily] {status_word}: {problem['title']} ({problem['difficulty']})"
    body = textwrap.dedent(f"""
        Daily LeetCode Auto-Solver Report
        ==================================
        Date       : {time.strftime('%Y-%m-%d')}
        Problem    : {problem['title']}
        Difficulty : {problem['difficulty']}
        Link       : {problem['link']}

        Result
        ------
        {final_desc}

        Submitted Code
        --------------
        {final_code}
    """).strip()

    try:
        send_email(subject, body)
    except Exception as e:
        print(f"Email failed: {e}")

    if not accepted:
        sys.exit(1)


if __name__ == "__main__":
    main()
