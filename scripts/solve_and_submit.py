"""
LeetCode Auto-Solver & Submitter
----------------------------------
Flow:
  1. Pick a random problem from LeetCode's full problem set
  2. Fetch its full content
  3. Ask Gemini to generate a Python3 solution
  4. Submit via LeetCode's internal submit endpoint
  5. Poll for verdict; retry up to 3 times on failure
  6. Send an email summary
"""

import os
import sys
import time
import re
import random
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
EMAIL_FROM       = os.environ["EMAIL_FROM"]
EMAIL_PASSWORD   = os.environ["EMAIL_PASSWORD"]
EMAIL_TO         = os.environ["EMAIL_TO"]

# ── constants ─────────────────────────────────────────────────────────────────
LC_GRAPHQL   = "https://leetcode.com/graphql"
LC_ORIGIN    = "https://leetcode.com"
MAX_RETRIES  = 3
POLL_DELAY   = 3
POLL_TIMEOUT = 60

HEADERS = {
    "User-Agent":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer":      "https://leetcode.com",
    "x-csrftoken":  CSRF_TOKEN,
    "Cookie":       f"LEETCODE_SESSION={LEETCODE_SESSION}; csrftoken={CSRF_TOKEN}",
    "Content-Type": "application/json",
    "Origin":       LC_ORIGIN,
}

# ── Gemini setup ──────────────────────────────────────────────────────────────
client = genai.Client(api_key=GEMINI_API_KEY)


# ─────────────────────────────────────────────────────────────────────────────
# Step 1a – Pick a random problem slug from the full problem list
# ─────────────────────────────────────────────────────────────────────────────
def pick_random_problem_slug() -> str:
    """
    Fetches a random page of LeetCode problems and returns one slug at random.
    Total problems ~3300+; we jump to a random offset and pick from that page.
    """
    # First get the total count
    count_query = """
    query problemCount {
      allQuestionsCount {
        difficulty
        count
      }
    }
    """
    resp = requests.post(LC_GRAPHQL, json={"query": count_query},
                         headers=HEADERS, timeout=30)
    resp.raise_for_status()
    counts = resp.json()["data"]["allQuestionsCount"]
    total  = next((c["count"] for c in counts if c["difficulty"] == "All"), 3000)
    print(f"  Total problems available: {total}")

    # Pick a random offset (leave room for page size of 50)
    skip = random.randint(0, max(0, min(total, 3500) - 50))

    list_query = """
    query problemList($skip: Int, $limit: Int) {
      problemsetQuestionList: questionList(
        categorySlug: "algorithms"
        limit: $limit
        skip: $skip
        filters: {}
      ) {
        questions: data {
          titleSlug
          title
          difficulty
          isPaidOnly
        }
      }
    }
    """
    resp = requests.post(
        LC_GRAPHQL,
        json={"query": list_query, "variables": {"skip": skip, "limit": 50}},
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    questions = resp.json()["data"]["problemsetQuestionList"]["questions"]

    # Filter out premium (paid-only) problems
    free_questions = [q for q in questions if not q.get("isPaidOnly", False)]
    if not free_questions:
        raise RuntimeError("No free problems found on this page — try again.")

    chosen = random.choice(free_questions)
    print(f"  Randomly selected: {chosen['title']} ({chosen['difficulty']})")
    return chosen["titleSlug"]


# ─────────────────────────────────────────────────────────────────────────────
# Step 1b – Fetch full problem details by slug
# ─────────────────────────────────────────────────────────────────────────────
def fetch_problem_by_slug(slug: str) -> dict:
    """Fetch full problem content + Python3 starter code for a given slug."""
    query = """
    query questionData($titleSlug: String!) {
      question(titleSlug: $titleSlug) {
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
    """
    resp = requests.post(
        LC_GRAPHQL,
        json={"query": query, "variables": {"titleSlug": slug}},
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    question = resp.json()["data"]["question"]

    if question is None:
        raise RuntimeError(f"Problem '{slug}' not found or is premium-only.")

    starter = ""
    for snippet in (question.get("codeSnippets") or []):
        if snippet.get("langSlug") == "python3":
            starter = snippet.get("code", "")
            break

    return {
        "title":       question["title"],
        "slug":        question["titleSlug"],
        "difficulty":  question["difficulty"],
        "content":     question["content"],
        "starter":     starter,
        "question_id": question["questionId"],
        "link":        f"https://leetcode.com/problems/{question['titleSlug']}/",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 – Generate solution with Gemini
# ─────────────────────────────────────────────────────────────────────────────
def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def ask_gemini(problem: dict, previous_error: str = "") -> str:
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
        model="gemini-3.8-flash",
        contents=prompt,
    )
    code = response.text.strip()
    code = re.sub(r"^```(?:python3?)?\n?", "", code)
    code = re.sub(r"\n?```$", "", code)
    return code.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 – Submit to LeetCode
# ─────────────────────────────────────────────────────────────────────────────
def submit_solution(problem: dict, code: str) -> str:
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
    msg      = result.get("status_msg", "Unknown")
    accepted = (msg == "Accepted")

    if accepted:
        percentile = result.get("runtime_percentile", 0) or 0
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
    print("LeetCode Auto-Solver (Random Problem Mode)")
    print("=" * 60)

    # 1. Pick + fetch a random problem
    print("\n[1/4] Picking a random problem...")
    for _ in range(5):
        slug    = pick_random_problem_slug()
        problem = fetch_problem_by_slug(slug)
        if problem.get("starter"):
            break
        print(f"  Problem '{slug}' has no Python3 starter template, trying another...")
    else:
        raise RuntimeError("Could not find a problem with Python3 starter code.")
    print(f"  -> {problem['title']} ({problem['difficulty']}) -- {problem['link']}")

    # 2-4. Generate -> Submit -> Verify with retry loop
    print("\n[2/4] Generating solution with Gemini...")
    accepted   = False
    final_desc = "No submission made."
    final_code = ""
    error_msg  = ""

    for attempt in range(1, MAX_RETRIES + 1):
        print(f"\n  Attempt {attempt}/{MAX_RETRIES}")
        try:
            code = ask_gemini(problem, previous_error=error_msg)
            print(f"  Generated {len(code)} chars of code.")

            print("[3/4] Submitting to LeetCode...")
            sub_id = submit_solution(problem, code)
            print(f"  Submission ID: {sub_id}")

            print("[4/4] Waiting for verdict...")
            result               = poll_verdict(str(sub_id))
            accepted, final_desc = parse_verdict(result)
            final_code           = code
            print(f"  Verdict: {final_desc}")

            if accepted:
                break

            error_msg = final_desc
            print("  Not accepted -- retrying with error context.")
            time.sleep(5)

        except Exception as exc:
            error_msg  = str(exc)
            final_desc = f"Error on attempt {attempt}: {exc}"
            print(f"  Exception: {exc}")
            time.sleep(5)

    # 5. Email report
    status_word = "ACCEPTED" if accepted else "FAILED"
    subject = f"[LeetCode] {status_word}: {problem['title']} ({problem['difficulty']})"
    body = textwrap.dedent(f"""
        LeetCode Auto-Solver Report
        ============================
        Date       : {time.strftime('%Y-%m-%d %H:%M UTC')}
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
