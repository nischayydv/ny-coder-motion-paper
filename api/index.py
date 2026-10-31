#!/usr/bin/env python3
"""
NY CODER :: Question Viewer — Vercel serverless entrypoint.
Flask app exposed as `app`; Vercel's @vercel/python runtime auto-detects
and serves any WSGI-compatible `app` object from api/index.py.

Local dev:  vercel dev
Deploy:     vercel --prod
"""

import concurrent.futures
import html
import os
import re
import time

import requests
from flask import Flask, Response, jsonify, request

app = Flask(__name__)

# ---------- Configuration ----------
EXTERNAL_API = "https://learning.motion.ac.in/motioneducation/api/getsinglequestion"
SOLUTION_API = "https://learning.motion.ac.in/motioneducation/api/getviewsolution"
DEFAULT_PAPER_ID = int(os.environ.get("NYCODER_DEFAULT_PAPER_ID", "46921"))
SUBJECTS = ["Maths", "Physics", "Chemistry"]
PLANNER_TEST_ID = 0
# Override in Vercel > Project > Settings > Environment Variables.
USER_ID = os.environ.get("NYCODER_USER_ID", "833031")

UPSTREAM_TIMEOUT = 15
UPSTREAM_RETRIES = 2          # extra attempts on network errors / 429 / 5xx
MAX_PAGES_PER_SUBJECT = 200   # safety valve against a bad `last_page`

# Sent on every upstream call so requests look like they come from a real
# browser session on the site itself.
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://learning.motion.ac.in/",
    "Origin": "https://learning.motion.ac.in",
}

_DIGITS = re.compile(r"^\d{1,12}$")
_USER = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")


def _arg_int(name, default):
    """Digits-only query arg (paper_id / page / qid ...). Falls back to default."""
    v = request.args.get(name, default)
    v = str(v).strip()
    return v if _DIGITS.match(v) else str(default)


def _arg_user():
    v = (request.args.get("user_id") or USER_ID).strip()
    return v if _USER.match(v) else str(USER_ID)


def _arg_subject():
    v = request.args.get("subject", SUBJECTS[0])
    return v if v in SUBJECTS else SUBJECTS[0]


@app.after_request
def _security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    return resp


def _cached_json(payload, seconds=300):
    resp = jsonify(payload)
    resp.headers["Cache-Control"] = f"public, s-maxage={seconds}, stale-while-revalidate=3600"
    return resp


def upstream_get(url, params):
    """
    GET an upstream endpoint and return (ok, payload, http_status).

    Retries network errors, 429 and 5xx with backoff. Always tries to read a
    body, and reports non-JSON bodies (WAF/CDN challenge pages) distinctly.
    """
    last = (False, {"status": 502, "error": "Upstream unreachable"}, 502)
    for attempt in range(UPSTREAM_RETRIES + 1):
        if attempt:
            time.sleep(0.6 * attempt)
        try:
            resp = requests.get(url, params=params, headers=REQUEST_HEADERS, timeout=UPSTREAM_TIMEOUT)
        except requests.exceptions.RequestException as e:
            last = (False, {"status": 502, "error": f"Network error reaching upstream: {e}"}, 502)
            continue

        try:
            payload = resp.json()
        except ValueError:
            snippet = resp.text[:300].replace("\n", " ").strip()
            status = resp.status_code if resp.status_code >= 400 else 502
            last = (False, {
                "status": status,
                "error": f"Upstream returned a non-JSON response (HTTP {resp.status_code}): {snippet or '(empty body)'}",
            }, status)
            if status == 429 or status >= 500:
                continue
            return last

        if resp.status_code >= 400:
            msg = None
            if isinstance(payload, dict):
                msg = payload.get("message") or payload.get("error")
            last = (False, {"status": resp.status_code, "error": msg or f"Upstream HTTP {resp.status_code}"}, resp.status_code)
            if resp.status_code == 429 or resp.status_code >= 500:
                continue
            return last

        if not isinstance(payload, dict):
            return False, {"status": 502, "error": "Upstream returned an unexpected payload shape"}, 502
        return True, payload, resp.status_code
    return last


# ---------- Helper: fetch all pages for one subject ----------
def fetch_all_pages_for_subject(paper_id, subject, user_id):
    """
    Returns every question for the subject. If a later page fails, the
    questions already fetched are still returned together with an `error`,
    so the client can tell a *partial* subject from an empty one.
    """
    all_questions = []
    page = 1
    total_pages = None

    while page <= MAX_PAGES_PER_SUBJECT:
        params = {
            "subject": subject,
            "paper_id": paper_id,
            "planner_test_id": PLANNER_TEST_ID,
            "user_id": user_id,
            "page": page,
        }
        ok, data, _status = upstream_get(EXTERNAL_API, params)
        if not ok:
            return {
                "subject": subject,
                "questions": all_questions,
                "count": len(all_questions),
                "total_pages": total_pages,
                "error": f"page {page}: {data.get('error', 'Unknown upstream error')}",
            }

        questions_data = data.get("questions") or {}
        if not isinstance(questions_data, dict):
            questions_data = {}
        items = questions_data.get("data") or []
        all_questions.extend(items)

        if total_pages is None:
            try:
                total_pages = int(questions_data.get("last_page", 1) or 1)
            except (TypeError, ValueError):
                total_pages = 1

        if page >= total_pages:
            break
        page += 1

    return {
        "subject": subject,
        "questions": all_questions,
        "count": len(all_questions),
        "total_pages": total_pages,
    }


# ---------- Proxy endpoint for single question ----------
@app.route("/api")
def proxy_api():
    params = {
        "subject": _arg_subject(),
        "paper_id": _arg_int("paper_id", DEFAULT_PAPER_ID),
        "planner_test_id": _arg_int("planner_test_id", PLANNER_TEST_ID),
        "user_id": _arg_user(),
        "page": _arg_int("page", 1),
    }
    ok, payload, status = upstream_get(EXTERNAL_API, params)
    if not ok:
        return jsonify(payload), status
    return _cached_json(payload)


# ---------- Proxy endpoint for a question's solution ----------
@app.route("/api/solution")
def solution_api():
    qid = request.args.get("qid", "").strip()
    if not _DIGITS.match(qid):
        return jsonify({"status": 400, "error": "qid is required and must be numeric"}), 400

    params = {
        "subject": _arg_subject(),
        "paper_id": _arg_int("paper_id", DEFAULT_PAPER_ID),
        "planner_test_id": _arg_int("planner_test_id", PLANNER_TEST_ID),
        "user_id": _arg_user(),
        "qid": qid,
    }
    ok, payload, status = upstream_get(SOLUTION_API, params)
    if not ok:
        return jsonify(payload), status
    return _cached_json(payload)


# ---------- Full paper (all subjects, all pages) ----------
@app.route("/api/full_paper")
def full_paper_api():
    paper_id = _arg_int("paper_id", DEFAULT_PAPER_ID)
    user_id = _arg_user()

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        future_to_subject = {
            executor.submit(fetch_all_pages_for_subject, paper_id, subject, user_id): subject
            for subject in SUBJECTS
        }
        for future in concurrent.futures.as_completed(future_to_subject):
            subject = future_to_subject[future]
            try:
                results[subject] = future.result()
            except Exception as e:  # noqa: BLE001 - never let one subject kill the response
                results[subject] = {"subject": subject, "questions": [], "count": 0, "error": str(e)}

    has_error = any(r.get("error") for r in results.values())
    resp = jsonify({"status": 200, "paper_id": paper_id, "subjects": results})
    # Never cache a response that contains a failure.
    resp.headers["Cache-Control"] = "no-store" if has_error else "public, s-maxage=300, stale-while-revalidate=3600"
    return resp


# ---------- HTML page (embedded, NY CODER theme) ----------
HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NY CODER :: Question Viewer</title>
<style>
    :root{
        --green:#00ff41;
        --green-dim:#00b32c;
        --green-glow: 0 0 6px #00ff41, 0 0 14px #00ff4180, 0 0 28px #00ff4140;
        --bg:#020402;
        --panel:#050b06;
        --border:#0f3d1c;
        --red:#ff2d55;
        --amber:#ffb000;
    }
    *{ box-sizing:border-box; }
    html,body{ height:100%; }
    body{
        margin:0; padding:20px;
        background: var(--bg);
        color: var(--green);
        font-family: 'Consolas','Courier New', ui-monospace, monospace;
        overflow-x:hidden;
        position:relative;
    }

    #matrixCanvas{
        position:fixed; inset:0; width:100%; height:100%;
        z-index:0; opacity:0.5; pointer-events:none;
    }

    .scanlines{
        position:fixed; inset:0; z-index:5; pointer-events:none;
        background: repeating-linear-gradient(
            to bottom,
            rgba(0,255,65,0.035) 0px,
            rgba(0,255,65,0.035) 1px,
            transparent 2px,
            transparent 3px
        );
        mix-blend-mode: overlay;
    }
    .crt-flicker{
        position:fixed; inset:0; z-index:6; pointer-events:none;
        background: radial-gradient(ellipse at center, rgba(0,255,65,0.03) 0%, rgba(0,0,0,0.35) 100%);
        animation: flicker 6s infinite;
    }
    @keyframes flicker{
        0%,19%,21%,23%,25%,54%,56%,100%{ opacity:1; }
        20%,24%,55%{ opacity:0.86; }
    }

    .wrap{ position:relative; z-index:10; max-width:1200px; margin:0 auto; }

    .container{
        background: linear-gradient(180deg, var(--panel), #030603);
        border: 1px solid var(--border);
        border-radius: 10px;
        box-shadow: var(--green-glow), 0 0 60px rgba(0,255,65,0.08) inset;
        padding: 0 0 25px 0;
        animation: powerOn 1s ease-out;
    }
    @keyframes powerOn{
        0%{ opacity:0; transform: scaleY(0.02); filter: brightness(3); }
        30%{ opacity:1; transform: scaleY(1); }
        60%{ filter: brightness(1.4); }
        100%{ filter: brightness(1); }
    }
    .titlebar{
        display:flex; align-items:center; gap:10px;
        padding:10px 16px; border-bottom:1px solid var(--border);
        background: linear-gradient(180deg,#0a170b,#050b06);
        border-radius:10px 10px 0 0;
    }
    .dot{ width:11px; height:11px; border-radius:50%; box-shadow:0 0 6px currentColor; }
    .dot.red{ background:#ff5f57; color:#ff5f57; }
    .dot.amber{ background:#febc2e; color:#febc2e; }
    .dot.green{ background:#28c840; color:#28c840; }
    .titlebar .path{ margin-left:10px; color:var(--green-dim); font-size:0.82em; letter-spacing:0.5px; }
    .titlebar .status-led{ margin-left:auto; font-size:0.75em; color:var(--green); display:flex; align-items:center; gap:6px; }
    .status-led .pulse{ width:8px; height:8px; border-radius:50%; background:var(--green); box-shadow:0 0 8px var(--green); animation:pulse 1.4s infinite; }
    @keyframes pulse{ 0%,100%{ opacity:1; transform:scale(1);} 50%{ opacity:0.4; transform:scale(0.7);} }

    .content{ padding: 20px 28px 0 28px; }

    .brand{ text-align:center; margin-bottom:6px; }
    h1.glitch{
        font-size: 2.6em; letter-spacing:4px; margin:8px 0 2px 0;
        text-transform:uppercase;
        text-shadow: var(--green-glow);
        position:relative;
        animation: flickerText 4s infinite;
    }
    h1.glitch::before, h1.glitch::after{
        content: attr(data-text);
        position:absolute; left:0; top:0; width:100%;
        overflow:hidden; clip-path: inset(0 0 0 0);
    }
    h1.glitch::before{
        color:var(--red); left:2px; text-shadow:none;
        animation: glitchTop 3.2s infinite linear alternate-reverse;
    }
    h1.glitch::after{
        color:#00e5ff; left:-2px; text-shadow:none;
        animation: glitchBottom 2.6s infinite linear alternate-reverse;
    }
    @keyframes glitchTop{
        0%{ clip-path: inset(0 0 85% 0); transform:translate(-1px,-1px);}
        20%{ clip-path: inset(10% 0 60% 0); transform:translate(1px,0);}
        40%{ clip-path: inset(40% 0 30% 0); transform:translate(-2px,1px);}
        60%{ clip-path: inset(60% 0 10% 0); transform:translate(2px,0);}
        80%{ clip-path: inset(20% 0 55% 0); transform:translate(-1px,1px);}
        100%{ clip-path: inset(75% 0 5% 0); transform:translate(1px,-1px);}
    }
    @keyframes glitchBottom{
        0%{ clip-path: inset(70% 0 5% 0); transform:translate(1px,1px);}
        25%{ clip-path: inset(30% 0 50% 0); transform:translate(-2px,0);}
        50%{ clip-path: inset(5% 0 80% 0); transform:translate(2px,-1px);}
        75%{ clip-path: inset(50% 0 20% 0); transform:translate(-1px,0);}
        100%{ clip-path: inset(15% 0 65% 0); transform:translate(1px,1px);}
    }
    @keyframes flickerText{
        0%,100%{ opacity:1; } 92%{ opacity:1; } 93%{ opacity:0.55; } 94%{ opacity:1; } 96%{ opacity:0.75; } 97%{ opacity:1; }
    }

    .subtitle{ color: var(--green-dim); font-size:0.85em; letter-spacing:2px; margin-bottom:14px; }

    .slogan-box{
        border:1px dashed var(--border); border-radius:6px;
        padding:10px 16px; margin: 0 auto 20px auto; max-width:820px;
        background: rgba(0,255,65,0.03);
    }
    .slogan-line{
        font-size:0.95em; white-space:nowrap; overflow:hidden; border-right:2px solid var(--green);
        width:0; margin:4px auto; animation: typing 3.5s steps(60,end) forwards, blinkCursor 0.8s step-end infinite;
        display:block; text-align:center;
    }
    @keyframes typing{ from{ width:0; } to{ width:100%; } }
    @keyframes blinkCursor{ 50%{ border-color:transparent; } }

    .controls { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-bottom: 20px;
        border-top:1px solid var(--border); border-bottom:1px solid var(--border); padding:14px 0; }
    .controls label { font-weight: 600; color:var(--green-dim); font-size:0.85em; text-transform:uppercase; letter-spacing:1px;}
    .controls input[type="number"], .controls input[type="text"] {
        padding: 9px 12px; width: 150px; background:#02120a; color:var(--green);
        border: 1px solid var(--border); border-radius: 4px; font-size: 1em; font-family:inherit;
        box-shadow: inset 0 0 8px rgba(0,255,65,0.15);
    }
    .controls input[type="number"]:focus, .controls input[type="text"]:focus{ outline:none; border-color:var(--green); box-shadow: var(--green-glow); }

    .controls button {
        padding: 9px 20px; background: transparent; color: var(--green); border: 1px solid var(--green);
        border-radius: 4px; font-weight: 600; cursor: pointer; transition: 0.15s; font-family:inherit;
        letter-spacing:1px; text-transform:uppercase; font-size:0.85em; position:relative; overflow:hidden;
    }
    .controls button:hover { background: var(--green); color:#001a05; box-shadow: var(--green-glow); }
    .controls button:active{ transform: scale(0.96); }
    .controls button.secondary { border-color: var(--amber); color: var(--amber); }
    .controls button.secondary:hover { background: var(--amber); color:#231800; box-shadow:0 0 14px var(--amber); }

    .controls button.pdf-btn { border-color: var(--red); color: var(--red); }
    .controls button.pdf-btn:hover { background: var(--red); color:#2a0008; box-shadow:0 0 14px var(--red); }
    .controls button.pdf-btn:disabled { border-color:#4a1a24; color:#7a3a44; cursor:not-allowed; background:transparent; box-shadow:none; }

    .mode-toggle { margin-left: auto; display: flex; gap: 14px; align-items: center; }
    .mode-toggle label { font-weight: 400; cursor: pointer; color:var(--green); text-transform:none; letter-spacing:0; }
    .mode-toggle input[type="radio"] { margin-right: 4px; accent-color: var(--green); }

    .status { margin: 10px 0; font-weight: 500; font-size:0.9em; }
    .status::before{ content:"root@nycoder:~$ "; color:var(--green-dim); }
    .error { color: var(--red); }
    .error::before{ content:"root@nycoder:~$ "; color:#ff8fa3; }
    .loading { color: #00e5ff; }
    .loading::before{ content:"root@nycoder:~$ "; color:#79e9ff; }

    .subject-section { margin: 30px 0 40px 0; border-top: 1px solid var(--border); padding-top: 20px; }
    .subject-section:first-of-type { border-top: none; padding-top: 0; }
    .subject-header { display: flex; justify-content: space-between; align-items: center;
        background: rgba(0,255,65,0.05); padding: 10px 20px; border-radius: 6px; border:1px solid var(--border); }
    .subject-header h2 { margin: 0; text-transform:uppercase; letter-spacing:2px; text-shadow: 0 0 8px rgba(0,255,65,0.6); }
    .subject-header .count { font-size: 0.9em; color: var(--green-dim); }
    .question-item { border: 1px solid var(--border); border-radius: 6px; padding: 15px 20px; margin: 12px 0;
        background: rgba(0,20,8,0.55); transition: 0.2s; }
    .question-item:hover{ border-color: var(--green); box-shadow: 0 0 14px rgba(0,255,65,0.18); }
    .question-item .qid { color: var(--green-dim); font-size: 0.85em; }
    .question-item .toughness { display: inline-block; background: transparent; border:1px solid var(--amber);
        color:var(--amber); padding: 0 14px; border-radius: 20px; font-size: 0.75em; font-weight: 700; text-transform:uppercase; }
    .question-item .q-text { margin-top: 10px; font-size: 1.05em; line-height: 1.7; color:#d9ffe6; }

    .nav-buttons { display: flex; justify-content: space-between; gap: 12px; margin: 20px 0; }
    .nav-buttons button { padding: 10px 24px; background: transparent; color: var(--green); border: 1px solid var(--green);
        border-radius: 6px; font-weight: 600; cursor: pointer; transition: 0.2s; flex: 1; text-transform:uppercase; letter-spacing:1px; font-family:inherit; }
    .nav-buttons button:hover { background: var(--green); color:#001a05; box-shadow: var(--green-glow); }
    .nav-buttons button:disabled { border-color:#1a2e1c; color: #2c4a30; cursor: not-allowed; background:transparent; box-shadow:none; }
    .question-box { border: 1px solid var(--border); border-radius: 8px; padding: 20px;
        background: rgba(0,20,8,0.55); min-height: 150px; box-shadow: inset 0 0 20px rgba(0,255,65,0.06); }
    .page-info { text-align: center; margin-top: 15px; font-size: 0.95em; color: var(--green-dim); letter-spacing:1px; }

    .sol-btn{
        margin-top:12px; display:inline-block; padding: 7px 16px; background: transparent;
        color:#00e5ff; border:1px solid #00e5ff; border-radius:4px; font-weight:600; cursor:pointer;
        letter-spacing:1px; text-transform:uppercase; font-size:0.78em; font-family:inherit; transition:0.15s;
    }
    .sol-btn:hover{ background:#00e5ff; color:#00222a; box-shadow:0 0 14px #00e5ff; }
    .sol-btn.open{ border-color: var(--amber); color: var(--amber); }
    .sol-btn.open:hover{ background: var(--amber); color:#231800; box-shadow:0 0 14px var(--amber); }

    .sol-panel{
        margin-top:14px; padding:16px 18px; border:1px solid var(--border); border-radius:6px;
        background: rgba(0,255,65,0.03); animation: powerOn 0.4s ease-out;
    }
    .sol-panel .sol-loading{ color:#00e5ff; font-size:0.85em; }
    .sol-panel .sol-loading::before{ content:"root@nycoder:~$ fetching solution... "; color:#79e9ff; }
    .sol-panel .sol-error{ color: var(--red); font-size:0.85em; }

    .opt-grid{ display:grid; grid-template-columns: 1fr 1fr; gap:10px; margin-bottom:16px; }
    @media (max-width: 640px){ .opt-grid{ grid-template-columns: 1fr; } }
    .opt-box{
        border:1px solid var(--border); border-radius:6px; padding:10px 14px;
        background: rgba(0,20,8,0.5); font-size:0.95em; display:flex; gap:10px; align-items:flex-start;
    }
    .opt-box .opt-letter{ color:var(--green-dim); font-weight:700; min-width:18px; }
    .opt-box.correct{
        border-color: var(--green); background: rgba(0,255,65,0.1);
        box-shadow: var(--green-glow);
    }
    .opt-box.correct .opt-letter{ color:var(--green); }
    .opt-box.correct::after{ content:"✔ CORRECT"; margin-left:auto; font-size:0.7em; color:var(--green); letter-spacing:1px; }

    .sol-heading{
        color:var(--amber); text-transform:uppercase; letter-spacing:2px; font-size:0.85em;
        margin: 4px 0 10px 0; border-bottom:1px dashed var(--border); padding-bottom:6px;
    }
    .sol-heading::before{ content:"root@nycoder:~$ cat solution.log"; display:block; color:var(--green-dim); font-size:0.9em; margin-bottom:4px; letter-spacing:0.5px; }
    .sol-text{ line-height:1.7; color:#d9ffe6; font-size:1em; }
    .sol-text p{ margin:0.6em 0; }

    .footer-tag{
        text-align:center; margin-top:26px; padding-top:16px; border-top:1px solid var(--border);
        color: var(--green-dim); font-size:0.78em; letter-spacing:1.5px;
    }
    .footer-tag b{ color:var(--green); }

    .hidden { display: none; }

    ::selection{ background: var(--green); color:#001a05; }
    ::-webkit-scrollbar{ width:10px; }
    ::-webkit-scrollbar-track{ background:#020402; }
    ::-webkit-scrollbar-thumb{ background: var(--border); border-radius:5px; }
    ::-webkit-scrollbar-thumb:hover{ background: var(--green-dim); }

    /* Fill-in-the-blank marker (used on screen AND in the exported PDF) */
    .blank{
        display:inline-block; min-width:4em; border-bottom:2px solid currentColor;
        text-decoration:none; line-height:1.1; margin:0 0.2em; vertical-align:baseline;
    }
    .q-text img, .sol-text img, .opt-box img{ max-width:100%; height:auto; }

    /* Hidden staging area used while building the PDF */
    .print-only{ display:none; }
</style>
</head>
<body>

<canvas id="matrixCanvas"></canvas>
<div class="scanlines"></div>
<div class="crt-flicker"></div>

<div class="wrap">
  <div class="container">

    <div class="titlebar">
        <span class="dot red"></span>
        <span class="dot amber"></span>
        <span class="dot green"></span>
        <span class="path">/root/nycoder/question_viewer.sh — LIVE SESSION</span>
        <span class="status-led"><span class="pulse"></span> CONNECTED</span>
    </div>

    <div class="content">

        <div class="brand">
            <h1 class="glitch" data-text="NY CODER">NY CODER</h1>
            <div class="subtitle">[ OSINT MODULE :: QUESTION EXTRACTION TERMINAL v2.7 ]</div>
        </div>

        <div class="slogan-box">
            <span class="slogan-line">We live in the code. We fight in the code. No system is safe.</span>
        </div>

        <div class="controls">
            <label for="paperId">Target Paper_ID ::</label>
            <input type="number" id="paperId" value="__DEFAULT_PAPER__" min="1">
            <label for="userId">User_ID ::</label>
            <input type="text" id="userId" value="__DEFAULT_USER_ID__" placeholder="0000">
            <button id="goBtn">&gt; Execute</button>
            <button id="resetBtn" class="secondary">&gt; Reset</button>
            <button id="pdfBtn" class="pdf-btn">&gt; Download PDF</button>

            <div class="mode-toggle">
                <span style="margin-right: 6px; color:var(--green-dim); text-transform:uppercase; font-size:0.85em; letter-spacing:1px;">Mode:</span>
                <label><input type="radio" name="mode" value="single" checked> Single</label>
                <label><input type="radio" name="mode" value="full"> Full Dump</label>
            </div>
        </div>
        <div class="subtitle" style="text-align:left; margin: -12px 0 16px 0;">
            Tip: solutions/attempt-status are looked up per user on the upstream API — if <b>View Solution</b> errors out, try setting User_ID to your real account id instead of the default placeholder.
        </div>

        <div id="status" class="status"></div>
        <div id="pdfStatus" class="status hidden"></div>

        <!-- Single-question view -->
        <div id="singleView">
            <div class="question-box">
                <div class="qid" id="qidDisplay">QID: —</div>
                <div class="toughness" id="toughnessDisplay">—</div>
                <div class="q-text" id="qTextDisplay">Enter a Paper ID and click <strong>Execute</strong>.</div>
                <button class="sol-btn hidden" id="singleSolBtn">&gt; View Solution</button>
                <div class="sol-panel hidden" id="singleSolPanel"></div>
            </div>
            <div class="nav-buttons">
                <button id="prevBtn" disabled>◀ Prev Byte</button>
                <button id="nextBtn" disabled>Next Byte ▶</button>
            </div>
            <div class="page-info" id="pageInfo">Page 0 / 0</div>
        </div>

        <!-- Full-paper view -->
        <div id="fullView" class="hidden">
            <div id="fullContent"></div>
        </div>

        <div class="footer-tag">
            <b>ACCESS GRANTED</b> :: Trace me if you can. || NY CODER © all rights reversed.
        </div>

    </div>
  </div>
</div>

<!-- Print-only, professionally formatted PDF/exam-paper layout.
     Hidden on screen; @media print swaps visibility so this is the only
     thing that ends up in the generated PDF when the user hits Download PDF. -->
<div id="printContainer" class="print-only"></div>

<script>
window.MathJax = {
    loader: { load: ['[tex]/noerrors', '[tex]/noundefined'] },
    tex: {
        inlineMath: [['$', '$'], ['\\(', '\\)']],
        displayMath: [['$$', '$$'], ['\\[', '\\]']],
        packages: { '[+]': ['noerrors', 'noundefined'] },
        macros: {
            hspace: ['\\kern #1', 1],   // MathJax has no \hspace; map it so blanks keep their width
            vspace: ['', 1]
        }
    },
    noundefined: { color: 'inherit', background: '', size: '' },
    chtml: { fontURL: 'https://cdn.jsdelivr.net/npm/mathjax@3/es5/output/chtml/fonts/woff-v2' }
};
</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>

<script>
(function(){
    const canvas = document.getElementById('matrixCanvas');
    const ctx = canvas.getContext('2d');
    let w, h, cols, drops;
    const chars = 'アカサタナハマヤラワ0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ$#@%&*';
    function resize(){
        w = canvas.width = window.innerWidth;
        h = canvas.height = window.innerHeight;
        const fontSize = 15;
        cols = Math.floor(w / fontSize);
        drops = new Array(cols).fill(1);
    }
    resize();
    window.addEventListener('resize', resize);
    function draw(){
        ctx.fillStyle = 'rgba(2,4,2,0.08)';
        ctx.fillRect(0,0,w,h);
        ctx.fillStyle = '#00ff41';
        ctx.font = '15px monospace';
        for(let i=0;i<drops.length;i++){
            const text = chars[Math.floor(Math.random()*chars.length)];
            ctx.fillText(text, i*15, drops[i]*15);
            if(drops[i]*15 > h && Math.random() > 0.975) drops[i] = 0;
            drops[i]++;
        }
        requestAnimationFrame(draw);
    }
    draw();
})();
</script>

<script>
    (function() {
        const paperInput = document.getElementById('paperId');
        const userIdInput = document.getElementById('userId');
        const goBtn = document.getElementById('goBtn');
        const resetBtn = document.getElementById('resetBtn');
        const prevBtn = document.getElementById('prevBtn');
        const nextBtn = document.getElementById('nextBtn');
        const statusDiv = document.getElementById('status');
        const qidDisplay = document.getElementById('qidDisplay');
        const toughnessDisplay = document.getElementById('toughnessDisplay');
        const qTextDisplay = document.getElementById('qTextDisplay');
        const pageInfo = document.getElementById('pageInfo');
        const singleSolBtn = document.getElementById('singleSolBtn');
        const singleSolPanel = document.getElementById('singleSolPanel');
        const pdfBtn = document.getElementById('pdfBtn');
        const pdfStatus = document.getElementById('pdfStatus');
        const printContainer = document.getElementById('printContainer');

        const singleView = document.getElementById('singleView');
        const fullView = document.getElementById('fullView');
        const fullContent = document.getElementById('fullContent');

        const modeRadios = document.querySelectorAll('input[name="mode"]');

        let currentPage = 1;
        let totalPages = 0;
        let paperId = parseInt(paperInput.value) || __DEFAULT_PAPER__;
        let isLoading = false;
        let currentMode = 'single';
        let currentSingleQuestion = null; // { qid, subject, toughness, q_text } for the on-screen single question
        let lastFullPaperData = null;      // last successful /api/full_paper response, for PDF export

        function switchMode(mode) {
            currentMode = mode;
            if (mode === 'single') {
                singleView.classList.remove('hidden');
                fullView.classList.add('hidden');
                if (paperId > 0) {
                    fetchQuestion(currentPage);
                }
            } else {
                singleView.classList.add('hidden');
                fullView.classList.remove('hidden');
                fetchFullPaper();
            }
        }

        modeRadios.forEach(radio => {
            radio.addEventListener('change', function() {
                if (this.checked) {
                    switchMode(this.value);
                }
            });
        });

        function currentUserId() {
            return (userIdInput.value || '').trim() || '0000';
        }

        // ---------- Upstream HTML cleaning ----------
        // The upstream API returns question/option/solution text as full,
        // standalone "<html>...<body>..." strings. Before any of it is shown
        // or exported we:
        //   1. parse each fragment in isolation (no cross-contamination),
        //   2. strip scripts/styles/classes/ids/event handlers (security, and
        //      so upstream CSS can never hide or restyle our own page),
        //   3. strip inline styles that make text invisible or unprintable
        //      (white text, visibility:hidden, opacity, absolute positioning),
        //   4. make relative image URLs absolute,
        //   5. turn every kind of "blank" into a visible .blank marker.
        const SITE_ORIGIN = 'https://learning.motion.ac.in';
        const MATH_SPLIT_RE = /(\$\$[\s\S]*?\$\$|\$(?:\\.|[^$\\])+\$|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\])/g;
        const STYLE_DROP = ['color', 'background', 'background-color', 'background-image', 'visibility',
            'opacity', 'position', 'top', 'left', 'right', 'bottom', 'z-index', 'overflow', 'overflow-x',
            'overflow-y', 'clip', 'clip-path', 'font-family', 'font-size', 'line-height', 'transform',
            'filter', 'text-shadow', '-webkit-text-fill-color', 'text-indent', 'letter-spacing', 'height',
            'max-height', 'min-height'];

        function makeBlank(len) {
            const span = document.createElement('span');
            span.className = 'blank';
            span.style.minWidth = Math.min(Math.max(len * 0.45, 3), 10).toFixed(1) + 'em';
            span.textContent = '\u00A0';
            return span;
        }

        // MathJax has no \hspace / empty \underline{}; both are typical blank encodings.
        function fixMathSource(tex) {
            return tex
                .replace(/\\hspace\*?\s*\{\s*([^{}]*?)\s*\}/g, function(m, dim) {
                    dim = dim.replace(/\s+/g, '');
                    return /^-?[\d.]+(em|ex|mu|px|mm|cm|in|pt|pc)$/i.test(dim) ? '\\kern ' + dim + ' ' : '\\kern 3em ';
                })
                .replace(/\\vspace\*?\s*\{[^{}]*\}/g, '')
                .replace(/\\underline\s*\{\s*\}/g, '\\underline{\\kern 3em}')
                .replace(/^\$\s*\$$|^\\\(\s*\\\)$/, '');   // math that became empty -> drop it
        }

        function isEmptyText(el) {
            return el.textContent.replace(/[\s\u00A0\u200B]/g, '') === '' &&
                !el.querySelector('img, svg, mjx-container, table');
        }

        function normalizeFragmentDom(root) {
            // 1. remove dangerous / page-affecting elements
            root.querySelectorAll('script, style, link, meta, iframe, object, embed, form, base, noscript, title, template')
                .forEach(function(n) { n.remove(); });

            // 2. inputs become blanks
            root.querySelectorAll('input, textarea, select').forEach(function(el) {
                const t = (el.getAttribute('type') || 'text').toLowerCase();
                if (['radio', 'checkbox', 'hidden', 'button', 'submit'].indexOf(t) >= 0) { el.remove(); return; }
                const len = parseInt(el.getAttribute('size') || el.getAttribute('maxlength') || '8', 10) || 8;
                el.replaceWith(makeBlank(len));
            });

            // 3. attribute / style cleaning
            root.querySelectorAll('*').forEach(function(el) {
                Array.from(el.attributes).forEach(function(a) {
                    const n = a.name.toLowerCase();
                    if (n.indexOf('on') === 0 || ['srcdoc', 'formaction', 'color', 'face', 'bgcolor', 'background',
                        'id', 'contenteditable', 'autofocus', 'srcset', 'crossorigin', 'loading'].indexOf(n) >= 0) {
                        el.removeAttribute(a.name);
                    }
                });
                // keep only our own "blank" class
                const keepBlank = el.classList.contains('blank');
                el.removeAttribute('class');
                if (keepBlank) el.classList.add('blank');

                if (el.hasAttribute('style')) {
                    const isImg = el.tagName === 'IMG';
                    STYLE_DROP.forEach(function(p) {
                        if (isImg && (p === 'height' || p === 'max-height' || p === 'min-height')) return;
                        el.style.removeProperty(p);
                    });
                    if (!el.getAttribute('style').trim()) el.removeAttribute('style');
                }
            });

            // 4. images: absolute URLs, safe schemes only
            root.querySelectorAll('img').forEach(function(img) {
                const src = img.getAttribute('src');
                if (!src) { img.remove(); return; }
                try {
                    const u = new URL(src.trim(), SITE_ORIGIN + '/');
                    if (u.protocol === 'http:' || u.protocol === 'https:' || u.protocol === 'data:') {
                        img.setAttribute('src', u.protocol === 'data:' ? src.trim() : u.href);
                    } else { img.remove(); }
                } catch (e) { img.remove(); }
                if (img.isConnected) img.setAttribute('loading', 'eager');
            });

            // 5. links: unwrap (neither useful nor safe in a printed paper)
            root.querySelectorAll('a').forEach(function(a) {
                const s = document.createElement('span');
                while (a.firstChild) s.appendChild(a.firstChild);
                a.replaceWith(s);
            });

            // 6. empty underlined / bordered elements are blanks
            root.querySelectorAll('u, ins, [style*="underline"], [style*="border-bottom"]').forEach(function(el) {
                if (el.classList.contains('blank')) return;
                if (el.tagName === 'TABLE' || el.tagName === 'TD' || el.tagName === 'TR' || el.tagName === 'TH') return;
                if (!isEmptyText(el)) return;
                const len = el.textContent.length || 6;
                const b = makeBlank(len);
                el.replaceWith(b);
            });

            // 7. text nodes: underscore runs -> blanks, math source repaired
            const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
            const textNodes = [];
            while (walker.nextNode()) textNodes.push(walker.currentNode);
            textNodes.forEach(function(node) {
                const text = node.nodeValue;
                if (!text || (text.indexOf('_') < 0 && text.indexOf('\\') < 0)) return;
                let changed = false;
                const frag = document.createDocumentFragment();
                text.split(MATH_SPLIT_RE).forEach(function(part, idx) {
                    if (!part) return;
                    if (idx % 2 === 1) {
                        const fixed = fixMathSource(part);
                        if (fixed !== part) changed = true;
                        frag.appendChild(document.createTextNode(fixed));
                        return;
                    }
                    part.split(/(_{2,})/).forEach(function(p, j) {
                        if (!p) return;
                        if (j % 2 === 1) { changed = true; frag.appendChild(makeBlank(p.length)); }
                        else frag.appendChild(document.createTextNode(p));
                    });
                });
                if (changed) node.parentNode.replaceChild(frag, node);
            });
        }

        function extractFragmentHtml(raw) {
            if (!raw) return '';
            try {
                const doc = new DOMParser().parseFromString(String(raw), 'text/html');
                if (!doc.body) return '';
                normalizeFragmentDom(doc.body);
                return doc.body.innerHTML;
            } catch (e) {
                console.warn('extractFragmentHtml failed:', e);
                const d = document.createElement('div');
                d.textContent = String(raw);   // never inject raw upstream HTML on failure
                return d.innerHTML;
            }
        }

        function isCorrectOpt(opt) {
            if (!opt) return false;
            const v = opt.is_correct;
            return v === true || v === 1 || String(v).trim() === '1' || String(v).trim().toLowerCase() === 'true';
        }

        // Fetches a URL and always returns the parsed JSON body — even on a
        // non-2xx response — so upstream error text (surfaced by our own
        // /api* routes) makes it to the UI instead of a bare "HTTP 500".
        async function fetchJSON(url) {
            let response;
            try {
                response = await fetch(url);
            } catch (networkErr) {
                throw new Error(`Network error: ${networkErr.message}`);
            }

            let data = null;
            try {
                data = await response.json();
            } catch (parseErr) {
                throw new Error(`Bad response (HTTP ${response.status}): could not parse JSON`);
            }

            if (!response.ok || data.status !== 200) {
                throw new Error(data.error || data.message || `HTTP ${response.status}`);
            }
            return data;
        }

        async function fetchQuestion(page) {
            if (isLoading) return;
            isLoading = true;
            statusDiv.textContent = 'Loading...';
            statusDiv.className = 'status loading';

            try {
                const params = new URLSearchParams({
                    paper_id: paperId,
                    page: page,
                    subject: 'Maths',
                    planner_test_id: 0,
                    user_id: currentUserId()
                });
                const data = await fetchJSON(`/api?${params.toString()}`);
                renderSingle(data);
                statusDiv.textContent = 'Loaded';
                statusDiv.className = 'status';
            } catch (err) {
                statusDiv.textContent = `Error: ${err.message}`;
                statusDiv.className = 'status error';
                prevBtn.disabled = true;
                nextBtn.disabled = true;
                pageInfo.textContent = 'Page 0 / 0';
                qidDisplay.textContent = 'QID: —';
                toughnessDisplay.textContent = '—';
                qTextDisplay.innerHTML = 'Error loading question.';
            } finally {
                isLoading = false;
            }
        }

        function renderSingle(data) {
            const questions = data.questions;
            if (!questions) {
                qidDisplay.textContent = 'QID: —';
                toughnessDisplay.textContent = '—';
                qTextDisplay.innerHTML = 'No question data.';
                pageInfo.textContent = 'Page 0 / 0';
                prevBtn.disabled = true;
                nextBtn.disabled = true;
                singleSolBtn.classList.add('hidden');
                singleSolPanel.classList.add('hidden');
                currentSingleQuestion = null;
                return;
            }

            const items = questions.data || [];
            currentPage = questions.current_page || 1;
            totalPages = questions.last_page || 0;

            pageInfo.textContent = `Page ${currentPage} of ${totalPages}`;
            prevBtn.disabled = (currentPage <= 1);
            nextBtn.disabled = (currentPage >= totalPages);

            if (items.length === 0) {
                qidDisplay.textContent = 'QID: —';
                toughnessDisplay.textContent = '—';
                qTextDisplay.innerHTML = 'No question on this page.';
                singleSolBtn.classList.add('hidden');
                singleSolPanel.classList.add('hidden');
                currentSingleQuestion = null;
                return;
            }

            const q = items[0];
            qidDisplay.textContent = `QID: ${q.qid}`;
            toughnessDisplay.textContent = q.toughness || '—';
            const cleanQText = extractFragmentHtml(q.q_text) || '(empty)';
            qTextDisplay.innerHTML = cleanQText;
            currentSingleQuestion = {
                qid: q.qid,
                subject: q.subject || 'Maths',
                toughness: q.toughness || '—',
                q_text: cleanQText
            };

            // Wire up the solution button for this question, resetting any
            // previously-open panel from the last question viewed.
            singleSolBtn.dataset.qid = q.qid;
            singleSolBtn.dataset.subject = q.subject || 'Maths';
            singleSolBtn.textContent = '> View Solution';
            singleSolBtn.classList.remove('hidden', 'open');
            singleSolPanel.classList.add('hidden');
            singleSolPanel.innerHTML = '';

            if (window.MathJax && MathJax.typesetPromise) {
                MathJax.typesetPromise([qTextDisplay]).catch(err => console.warn('MathJax error:', err));
            }
        }

        // ---------- Solution feature (shared by single view, full view, and PDF export) ----------
        const solutionCache = {}; // qid -> parsed solution record | { __error: message }

        function optionLetter(i) {
            return String.fromCharCode(65 + i); // 0 -> A, 1 -> B, ...
        }

        // Fetches (and caches) the options + correct-answer + solution record
        // for one question. Shared by the "View Solution" panel and the PDF
        // export, so a question the user already expanded on screen won't be
        // re-fetched when building the PDF, and vice versa.
        async function fetchSolutionRecord(qid, subject) {
            if (Object.prototype.hasOwnProperty.call(solutionCache, qid)) {
                return solutionCache[qid];
            }
            // Retry transient failures (rate limits, WAF hiccups, 5xx). Failures are
            // NOT cached, so a later attempt (or a re-export) can succeed.
            let lastErr = null;
            for (let attempt = 0; attempt < 4; attempt++) {
                try {
                    const params = new URLSearchParams({
                        subject: subject || 'Maths',
                        paper_id: paperId,
                        planner_test_id: 0,
                        user_id: currentUserId(),
                        qid: qid
                    });
                    const data = await fetchJSON(`/api/solution?${params.toString()}`);
                    const record = (data.data && data.data[0]) || null;
                    solutionCache[qid] = record;
                    return record;
                } catch (err) {
                    lastErr = err;
                    await new Promise(function(r) { setTimeout(r, 600 * (attempt + 1) * (attempt + 1)); });
                }
            }
            return { __error: lastErr ? lastErr.message : 'Unknown error' };
        }

        function renderSolutionPanel(panelEl, record) {
            if (!record) {
                panelEl.innerHTML = '<div class="sol-error">No solution data available.</div>';
                return;
            }
            if (record.__error) {
                panelEl.innerHTML = `<div class="sol-error">Error: ${record.__error}</div>`;
                return;
            }
            const opts = record.option || [];
            let html = '';
            if (opts.length) {
                html += '<div class="opt-grid">';
                opts.forEach((opt, i) => {
                    const correct = isCorrectOpt(opt);
                    html += `<div class="opt-box${correct ? ' correct' : ''}">
                        <span class="opt-letter">${optionLetter(i)}.</span>
                        <span>${extractFragmentHtml(opt.option)}</span>
                    </div>`;
                });
                html += '</div>';
            }
            html += '<div class="sol-heading">Solution</div>';
            html += `<div class="sol-text">${extractFragmentHtml(record.sol_text) || 'No written solution available for this question.'}</div>`;
            panelEl.innerHTML = html;

            if (window.MathJax && MathJax.typesetPromise) {
                MathJax.typesetPromise([panelEl]).catch(err => console.warn('MathJax error:', err));
            }
        }

        async function fetchSolution(qid, subject, btnEl, panelEl) {
            const isOpen = !panelEl.classList.contains('hidden');
            if (isOpen) {
                panelEl.classList.add('hidden');
                btnEl.textContent = '> View Solution';
                btnEl.classList.remove('open');
                return;
            }

            panelEl.classList.remove('hidden');
            btnEl.textContent = '> Hide Solution';
            btnEl.classList.add('open');

            const cached = Object.prototype.hasOwnProperty.call(solutionCache, qid) ? solutionCache[qid] : undefined;
            if (cached !== undefined) {
                renderSolutionPanel(panelEl, cached);
                return;
            }

            panelEl.innerHTML = '<div class="sol-loading">decrypting solution payload</div>';
            const record = await fetchSolutionRecord(qid, subject);
            renderSolutionPanel(panelEl, record);
        }

        singleSolBtn.addEventListener('click', function() {
            const qid = this.dataset.qid;
            const subject = this.dataset.subject;
            if (!qid) return;
            fetchSolution(qid, subject, singleSolBtn, singleSolPanel);
        });

        // Event delegation for solution buttons rendered inside the full-paper view
        fullContent.addEventListener('click', function(e) {
            const btn = e.target.closest('.sol-btn');
            if (!btn) return;
            const panel = btn.nextElementSibling;
            fetchSolution(btn.dataset.qid, btn.dataset.subject, btn, panel);
        });

        // ---------- PDF export ("Download PDF") ----------
        const PRINT_CSS = `
            @page { size: A4; margin: 18mm 16mm 20mm 16mm; }
            *{ box-sizing:border-box; -webkit-print-color-adjust:exact; print-color-adjust:exact; }
            html,body{ margin:0; padding:0; background:#fff; }
            body{ font-family:'Source Serif 4','Noto Serif','Open Sans',Georgia,'Times New Roman',serif;
                  font-size:11pt; line-height:1.5; color:#111; }
            .ui{ font-family:'Inter','Noto Sans','Open Sans',Arial,Helvetica,sans-serif; }
            p{ margin:0.35em 0; }
            .doc-head{ display:flex; justify-content:space-between; align-items:flex-end;
                       border-bottom:2.5pt solid #111; padding-bottom:3mm; margin-bottom:4mm; }
            .brand{ font-family:'Courier New','DejaVu Sans Mono',monospace; font-size:20pt; font-weight:700; letter-spacing:2px; }
            .brand span{ color:#b3261e; }
            .meta{ text-align:right; font-size:8.5pt; line-height:1.55; color:#444; }
            .legend{ display:flex; align-items:center; gap:2.5mm; font-size:8.5pt; color:#333; margin:0 0 4mm 0; }
            .legend .swatch{ width:9mm; height:4.5mm; border:1pt solid #1b8a3e; background:#dcf2e3; border-radius:1mm; }
            .subject{ font-size:12pt; font-weight:700; text-transform:uppercase; letter-spacing:1px;
                      background:#efefef; border-left:1.6mm solid #111; padding:1.6mm 3.5mm;
                      margin:6mm 0 4mm 0; break-after:avoid; page-break-after:avoid;
                      break-before:page; page-break-before:always; }
            .subject.first{ break-before:auto; page-break-before:auto; margin-top:2mm; }
            .q{ break-inside:avoid; page-break-inside:avoid; margin:0 0 5.5mm 0; padding-bottom:4mm; border-bottom:0.5pt solid #e2e2e2; }
            .q-head{ margin-bottom:1.2mm; break-after:avoid; }
            .q-num{ font-weight:700; font-size:11.5pt; margin-right:2.5mm; }
            .q-tag{ font-size:7pt; color:#555; border:0.6pt solid #999; border-radius:3mm; padding:0.3mm 2.4mm; text-transform:uppercase; letter-spacing:0.5px; }
            .q-text{ margin:0 0 3mm 0; overflow-wrap:anywhere; }
            .q-text img{ max-width:100%; max-height:95mm; height:auto; vertical-align:middle; }
            .q-text table, .opt .body table{ border-collapse:collapse; max-width:100% !important; margin:2mm 0; }
            .q-text td, .q-text th, .opt .body td, .opt .body th{ border:0.6pt solid #888; padding:0.8mm 2mm; }
            .blank{ display:inline-block; min-width:4em; border-bottom:1.1pt solid #111; text-decoration:none;
                    line-height:1.05; margin:0 0.25em; vertical-align:baseline; }
            .opts{ display:grid; gap:2mm 3.5mm; }
            .opts.cols-4{ grid-template-columns:repeat(4, minmax(0,1fr)); }
            .opts.cols-2{ grid-template-columns:repeat(2, minmax(0,1fr)); }
            .opts.cols-1{ grid-template-columns:minmax(0,1fr); }
            .opt{ display:flex; align-items:flex-start; gap:2.2mm; padding:1.5mm 2.2mm;
                  border:1pt solid transparent; border-radius:1.6mm; break-inside:avoid; }
            .opt .tag{ flex:none; width:5.4mm; height:5.4mm; border:0.8pt solid #777; border-radius:50%;
                       display:inline-flex; align-items:center; justify-content:center;
                       font-weight:700; font-size:8.5pt; line-height:1; margin-top:0.4mm; background:#fff; }
            .opt .body{ flex:1; min-width:0; overflow-wrap:anywhere; }
            .opt .body p{ margin:0; }
            .opt .body img{ max-width:100%; max-height:45mm; height:auto; }
            .opt .muted{ color:#888; }
            .opt .tick{ flex:none; width:4.6mm; height:4.6mm; margin-top:0.5mm; }
            .opt.correct{ background:#dcf2e3; border:1.2pt solid #1b8a3e; }
            .opt.correct .tag{ background:#1b8a3e; border-color:#1b8a3e; color:#fff; }
            .opt.correct .body{ font-weight:600; }
            .typed{ display:inline-flex; align-items:center; gap:2.5mm; padding:1.6mm 3.5mm; background:#dcf2e3;
                    border:1.2pt solid #1b8a3e; border-radius:1.6mm; font-weight:700; font-size:10pt; }
            .typed .lbl{ font-weight:600; font-size:8pt; text-transform:uppercase; letter-spacing:0.6px; color:#1b6f36; }
            .note{ font-size:8.5pt; padding:1.6mm 3mm; border-radius:1.4mm; background:#fff4e5; border:0.6pt solid #e0a040; color:#7a4a00; }
            .note.soft{ background:#f5f5f5; border-color:#ccc; color:#666; }
            .key{ break-before:page; page-break-before:always; }
            .key h2{ font-size:13pt; text-transform:uppercase; letter-spacing:1px; border-bottom:1.5pt solid #111; padding-bottom:2mm; margin:0 0 4mm 0; }
            .key-sub{ font-weight:700; font-size:9pt; text-transform:uppercase; color:#444; margin:5mm 0 2mm 0; }
            .key-grid{ display:grid; grid-template-columns:repeat(6, minmax(0,1fr)); gap:2mm; font-size:9.5pt; }
            .key-cell{ border:0.6pt solid #c9c9c9; border-radius:1.5mm; padding:1.4mm 1.6mm; text-align:center; break-inside:avoid; }
            .key-cell b{ color:#1b7f3b; }
            .key-cell.miss{ background:#fff4e5; border-color:#e0a040; }
            mjx-container[display="true"]{ margin:0.7em 0 !important; }
        `;

        const TICK_SVG = '<svg class="tick" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="8" fill="#1b8a3e"/><path d="M4.2 8.4l2.6 2.6 5-5.4" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';

        function escapeHtml(s) {
            return String(s).replace(/[&<>"']/g, function(c) {
                return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
            });
        }

        function htmlToText(html) {
            const d = new DOMParser().parseFromString(String(html || ''), 'text/html');
            return (d.body.textContent || '').trim();
        }

        // Runs async jobs with a concurrency cap (don't hammer the upstream API).
        async function mapWithConcurrency(items, limit, worker) {
            const results = new Array(items.length);
            let nextIndex = 0;
            async function runNext() {
                while (nextIndex < items.length) {
                    const i = nextIndex++;
                    results[i] = await worker(items[i], i);
                }
            }
            const workers = Array.from({ length: Math.min(limit, items.length) }, runNext);
            await Promise.all(workers);
            return results;
        }

        // All correct letters (multi-correct questions have more than one).
        function correctLettersFor(record) {
            if (!record || record.__error || !Array.isArray(record.option)) return [];
            const out = [];
            record.option.forEach(function(o, i) { if (isCorrectOpt(o)) out.push(optionLetter(i)); });
            return out;
        }

        // For integer / numerical questions that have no options.
        function typedAnswerFor(record) {
            if (!record || record.__error) return '';
            const keys = ['answer', 'ans', 'correct_answer', 'correct_ans', 'integer_answer', 'numerical_answer', 'numeric_answer'];
            for (const k of keys) {
                const v = record[k];
                if (v !== undefined && v !== null && String(v).trim() !== '') return htmlToText(v) || '';
            }
            return '';
        }

        function optionLayout(options) {
            const htmls = options.map(function(o) { return extractFragmentHtml(o.option); });
            let maxLen = 0, rich = false;
            htmls.forEach(function(h) {
                const d = new DOMParser().parseFromString(h, 'text/html');
                if (d.body.querySelector('img, table, svg')) rich = true;
                const t = (d.body.textContent || '').replace(/\\[a-zA-Z]+|[{}$^_\\]/g, '').trim();
                maxLen = Math.max(maxLen, t.length);
            });
            let cols = 1;
            if (!rich && options.length > 1) cols = maxLen <= 14 ? 4 : (maxLen <= 44 ? 2 : 1);
            if (cols === 4 && options.length % 4 !== 0 && options.length < 4) cols = 2;
            return { cols: cols, htmls: htmls };
        }

        function optionBodyHtml(html) {
            const d = new DOMParser().parseFromString(html || '', 'text/html');
            const empty = !(d.body.textContent || '').trim() && !d.body.querySelector('img, svg, table, .blank');
            return empty ? '<span class="muted">&mdash;</span>' : html;
        }

        function buildPrintHtml(numbered, meta) {
            const counts = {};
            numbered.forEach(function(it) { counts[it.subject] = (counts[it.subject] || 0) + 1; });
            const countText = Object.keys(counts).map(function(s) { return `${escapeHtml(s)}: ${counts[s]}`; }).join(' &nbsp;|&nbsp; ');

            let html = `<div class="doc-head ui">
                <div class="brand">NY <span>CODER</span></div>
                <div class="meta">Paper ID: ${escapeHtml(meta.paperId)}<br>${numbered.length} question${numbered.length === 1 ? '' : 's'} &mdash; ${countText}<br>Generated: ${escapeHtml(meta.date)}</div>
            </div>
            <div class="legend ui"><span class="swatch"></span> Correct option is highlighted in green and marked with a tick. Answer key on the last page.</div>`;

            let lastSubject = null;
            numbered.forEach(function(item) {
                if (item.subject !== lastSubject) {
                    html += `<div class="subject ui${lastSubject === null ? ' first' : ''}">${escapeHtml(item.subject)}</div>`;
                    lastSubject = item.subject;
                }
                const record = item.record;
                html += `<section class="q">`;
                html += `<div class="q-head ui"><span class="q-num">Q${item.num}.</span><span class="q-tag">${escapeHtml(item.q.toughness || '—')}</span></div>`;
                html += `<div class="q-text">${extractFragmentHtml(item.q.q_text) || '<span class="note">(question text unavailable)</span>'}</div>`;

                if (record && !record.__error && Array.isArray(record.option) && record.option.length) {
                    const lay = optionLayout(record.option);
                    html += `<div class="opts cols-${lay.cols} ui">`;
                    record.option.forEach(function(opt, i) {
                        const correct = isCorrectOpt(opt);
                        html += `<div class="opt${correct ? ' correct' : ''}">
                            <span class="tag">${optionLetter(i)}</span>
                            <div class="body">${optionBodyHtml(lay.htmls[i])}</div>
                            ${correct ? TICK_SVG : ''}
                        </div>`;
                    });
                    html += '</div>';
                } else if (record && record.__error) {
                    html += `<div class="note ui">Options could not be loaded for this question (QID ${escapeHtml(item.q.qid)}): ${escapeHtml(record.__error)}</div>`;
                } else {
                    const typed = typedAnswerFor(record);
                    if (typed) {
                        html += `<div class="typed ui"><span class="lbl">Answer</span> ${escapeHtml(typed)}</div>`;
                    } else {
                        html += `<div class="note soft ui">No options provided for this question (QID ${escapeHtml(item.q.qid)}).</div>`;
                    }
                }
                html += `</section>`;
            });

            html += `<section class="key ui"><h2>Answer Key</h2>`;
            lastSubject = null;
            let open = false;
            numbered.forEach(function(item) {
                if (item.subject !== lastSubject) {
                    if (open) html += '</div>';
                    html += `<div class="key-sub">${escapeHtml(item.subject)}</div><div class="key-grid">`;
                    open = true;
                    lastSubject = item.subject;
                }
                const letters = correctLettersFor(item.record);
                const typed = letters.length ? '' : typedAnswerFor(item.record);
                const ans = letters.length ? letters.join(', ') : (typed || '—');
                const miss = !letters.length && !typed;
                html += `<div class="key-cell${miss ? ' miss' : ''}">Q${item.num}: <b>${escapeHtml(ans)}</b></div>`;
            });
            if (open) html += '</div>';
            html += `</section>`;
            return html;
        }

        function statusMsg(cls, text) {
            pdfStatus.classList.remove('hidden');
            pdfStatus.className = 'status ' + cls;
            pdfStatus.textContent = text;
        }

        async function handleDownloadPdf() {
            if (pdfBtn.disabled) return;

            let groups = [];
            if (currentMode === 'single') {
                if (!currentSingleQuestion) { statusMsg('error', 'No question loaded to export yet.'); return; }
                groups = [{ subject: currentSingleQuestion.subject, questions: [currentSingleQuestion] }];
            } else {
                if (!lastFullPaperData) { statusMsg('error', 'No full paper loaded to export yet.'); return; }
                const subjects = lastFullPaperData.subjects || {};
                // Refuse to export a silently-truncated paper.
                const partial = ['Maths', 'Physics', 'Chemistry'].filter(function(s) {
                    return subjects[s] && subjects[s].error && (subjects[s].questions || []).length > 0;
                });
                if (partial.length) {
                    statusMsg('error', `Paper is incomplete (${partial.join(', ')} failed part-way). Click Execute to reload, then export again.`);
                    return;
                }
                groups = ['Maths', 'Physics', 'Chemistry']
                    .map(function(sub) { return { subject: sub, questions: (subjects[sub] && subjects[sub].questions) || [] }; })
                    .filter(function(g) { return g.questions.length > 0; });
            }

            const numbered = [];
            let n = 0;
            groups.forEach(function(g) { g.questions.forEach(function(q) { n++; numbered.push({ num: n, subject: g.subject, q: q }); }); });
            if (numbered.length === 0) { statusMsg('error', 'Nothing to export.'); return; }

            const originalBtnText = pdfBtn.textContent;
            pdfBtn.disabled = true;
            pdfBtn.textContent = '> Preparing...';
            const finish = function(cls, msg) {
                pdfBtn.disabled = false;
                pdfBtn.textContent = originalBtnText;
                printContainer.innerHTML = '';
                printContainer.style.cssText = '';
                if (msg) statusMsg(cls, msg); else pdfStatus.classList.add('hidden');
            };

            try {
                let done = 0;
                statusMsg('loading', `Compiling 0/${numbered.length} question(s) into PDF...`);
                await mapWithConcurrency(numbered, 5, async function(item) {
                    item.record = await fetchSolutionRecord(item.q.qid, item.subject);
                    done++;
                    pdfStatus.textContent = `Compiling ${done}/${numbered.length} question(s) into PDF...`;
                });

                const failed = numbered.filter(function(it) { return it.record && it.record.__error; });
                if (failed.length) {
                    const list = failed.slice(0, 12).map(function(it) { return 'Q' + it.num; }).join(', ') + (failed.length > 12 ? '…' : '');
                    const go = window.confirm(`Options could not be loaded for ${failed.length} question(s): ${list}.\n\nThe PDF would show a "could not be loaded" note for them.\n\nOK = download anyway, Cancel = stop (then try again).`);
                    if (!go) { finish('error', `Export cancelled — ${failed.length} question(s) had missing options. Try again.`); return; }
                }

                statusMsg('loading', 'Rendering equations...');
                printContainer.innerHTML = buildPrintHtml(numbered, {
                    paperId: paperId,
                    date: new Date().toLocaleString()
                });
                // Needs layout (off-screen) so MathJax can measure.
                printContainer.style.cssText = 'display:block; position:fixed; left:-9999px; top:0; width:178mm; background:#fff;';

                if (window.MathJax && MathJax.startup && MathJax.startup.promise) {
                    try { await MathJax.startup.promise; } catch (e) { console.warn('MathJax startup:', e); }
                }
                if (window.MathJax && MathJax.typesetPromise) {
                    try { await MathJax.typesetPromise([printContainer]); }
                    catch (e) { console.warn('MathJax typeset error (PDF):', e); }
                }
                const mathErrors = printContainer.querySelectorAll('mjx-merror, [data-mjx-error]').length;

                // MathJax adds its per-glyph rules through the CSSOM (insertRule), so they
                // are NOT in the <style> tag's text. Serialise from sheet.cssRules instead,
                // otherwise every equation prints blank.
                const mjxStyles = Array.from(document.querySelectorAll('style'))
                    .filter(function(s) { return s.id === 'MJX-CHTML-styles' || /mjx-container/.test(s.textContent); })
                    .map(function(s) {
                        let css = '';
                        try { css = Array.from(s.sheet.cssRules).map(function(r) { return r.cssText; }).join('\n'); } catch (e) { css = ''; }
                        if (!css) css = s.textContent;
                        return '<style>' + css + '</style>';
                    })
                    .join('\n');

                const standaloneHtml = `<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&family=Source+Serif+4:ital,wght@0,400;0,600;0,700;1,400&display=swap">
<style>${PRINT_CSS}</style>
${mjxStyles}
</head><body>${printContainer.innerHTML}</body></html>`;

                if (new Blob([standaloneHtml]).size > 4300000) {
                    throw new Error('Paper is too large for a single PDF request (4.3 MB limit). Export subject-by-subject or use Single mode.');
                }

                statusMsg('loading', 'Generating PDF on server...');
                const resp = await fetch('/api/pdf', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        html: standaloneHtml,
                        filename: `NY_CODER_Paper_${paperId}`,
                        footer: `NY CODER · Paper ${paperId}`
                    })
                });
                if (!resp.ok) {
                    const errJson = await resp.json().catch(function() { return {}; });
                    throw new Error(errJson.error || `HTTP ${resp.status}`);
                }
                const warnings = resp.headers.get('X-Pdf-Warnings') || '';
                const blob = await resp.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `NY_CODER_Paper_${paperId}.pdf`;
                document.body.appendChild(a);
                a.click();
                a.remove();
                setTimeout(function() { URL.revokeObjectURL(url); }, 10000);

                const notes = [];
                if (failed.length) notes.push(`${failed.length} question(s) missing options`);
                if (mathErrors) notes.push(`${mathErrors} equation(s) could not be rendered`);
                const brokenImgs = /broken-images=(\d+)/.exec(warnings);
                if (brokenImgs) notes.push(`${brokenImgs[1]} image(s) could not be loaded`);
                if (notes.length) finish('error', 'PDF downloaded, but: ' + notes.join('; ') + '. Check the marked spots.');
                else finish();
            } catch (err) {
                console.error(err);
                finish('error', `PDF generation failed: ${err.message}`);
            }
        }

        pdfBtn.addEventListener('click', handleDownloadPdf);

        async function fetchFullPaper() {
            if (isLoading) return;
            isLoading = true;
            statusDiv.textContent = 'Loading full paper...';
            statusDiv.className = 'status loading';

            try {
                const params = new URLSearchParams({ paper_id: paperId, user_id: currentUserId() });
                const data = await fetchJSON(`/api/full_paper?${params.toString()}`);
                renderFull(data);
                statusDiv.textContent = 'Full paper loaded';
                statusDiv.className = 'status';
            } catch (err) {
                statusDiv.textContent = `Error: ${err.message}`;
                statusDiv.className = 'status error';
                fullContent.innerHTML = '<p style="color:#ff2d55;">Failed to load full paper.</p>';
                lastFullPaperData = null;
            } finally {
                isLoading = false;
            }
        }

        function renderFull(data) {
            lastFullPaperData = data;
            const subjects = data.subjects || {};
            let html = '';

            let summary = `<div style="background:rgba(0,255,65,0.05);padding:12px 20px;border-radius:8px;margin-bottom:20px;border:1px solid var(--border);">
                <strong>Paper ID: ${data.paper_id}</strong> &nbsp;|&nbsp;
            `;
            for (const sub of ['Maths', 'Physics', 'Chemistry']) {
                const info = subjects[sub];
                const count = info ? info.count : 0;
                summary += `<span style="margin:0 10px;">${sub}: ${count} questions</span>`;
            }
            summary += `</div>`;
            html += summary;

            for (const sub of ['Maths', 'Physics', 'Chemistry']) {
                const info = subjects[sub];
                if (!info) continue;
                const questions = info.questions || [];
                const count = info.count || 0;

                html += `<div class="subject-section">`;
                html += `<div class="subject-header">
                    <h2>${sub}</h2>
                    <span class="count">${count} question${count !== 1 ? 's' : ''}</span>
                </div>`;

                if (count === 0) {
                    if (info.error) {
                        html += `<p class="sol-error" style="padding:10px 0;">Error loading ${sub}: ${info.error}</p>`;
                    } else {
                        html += `<p style="color:#3a5c3e;padding:10px 0;">No questions found for this subject.</p>`;
                    }
                } else {
                    if (info.error) {
                        html += `<p class="sol-error" style="padding:6px 0;">Warning: ${sub} loaded only partially — ${info.error}. Click Execute to retry.</p>`;
                    }
                    for (const q of questions) {
                        html += `<div class="question-item">`;
                        html += `<div class="qid">QID: ${q.qid}</div>`;
                        html += `<div class="toughness">${q.toughness || '—'}</div>`;
                        html += `<div class="q-text">${extractFragmentHtml(q.q_text) || '(empty)'}</div>`;
                        html += `<button class="sol-btn" data-qid="${q.qid}" data-subject="${sub}">&gt; View Solution</button>`;
                        html += `<div class="sol-panel hidden"></div>`;
                        html += `</div>`;
                    }
                }
                html += `</div>`;
            }

            fullContent.innerHTML = html;

            if (window.MathJax && MathJax.typesetPromise) {
                MathJax.typesetPromise([fullContent]).catch(err => console.warn('MathJax error:', err));
            }
        }

        function goToPage(page) {
            if (page < 1 || page > totalPages) return;
            fetchQuestion(page);
        }

        function resetAndFetch() {
            const newId = parseInt(paperInput.value);
            if (!newId || newId < 1) {
                statusDiv.textContent = 'Please enter a valid Paper ID.';
                statusDiv.className = 'status error';
                return;
            }
            paperId = newId;
            currentPage = 1;
            if (currentMode === 'single') {
                fetchQuestion(1);
            } else {
                fetchFullPaper();
            }
        }

        goBtn.addEventListener('click', resetAndFetch);
        resetBtn.addEventListener('click', function() {
            paperInput.value = __DEFAULT_PAPER__;
            resetAndFetch();
        });
        prevBtn.addEventListener('click', function() {
            if (currentPage > 1) goToPage(currentPage - 1);
        });
        nextBtn.addEventListener('click', function() {
            if (currentPage < totalPages) goToPage(currentPage + 1);
        });
        paperInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') goBtn.click();
        });

        window.addEventListener('load', function() {
            document.querySelector('input[name="mode"][value="single"]').checked = true;
            resetAndFetch();
        });
    })();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    """Serve the main HTML page."""
    page = (HTML_TEMPLATE
            .replace("__DEFAULT_PAPER__", str(int(DEFAULT_PAPER_ID)))
            .replace("__DEFAULT_USER_ID__", html.escape(str(USER_ID), quote=True)))
    return Response(page, mimetype="text/html")


# Note: no app.run() here — Vercel's @vercel/python runtime imports this
# module and calls the WSGI `app` object directly per-request. Keep this
# file's top-level `app` name unchanged.
