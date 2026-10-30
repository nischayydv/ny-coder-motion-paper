#!/usr/bin/env python3
"""
NY CODER :: Question Viewer — Vercel serverless entrypoint.
Flask app exposed as `app`; Vercel's @vercel/python runtime auto-detects
and serves any WSGI-compatible `app` object from api/index.py.
"""

from flask import Flask, request, jsonify, render_template_string
import requests
import concurrent.futures

app = Flask(__name__)

# ---------- Configuration ----------
EXTERNAL_API = "https://learning.motion.ac.in/motioneducation/api/getsinglequestion"
SOLUTION_API = "https://learning.motion.ac.in/motioneducation/api/getviewsolution"
DEFAULT_PAPER_ID = 46921
SUBJECTS = ["Maths", "Physics", "Chemistry"]
PLANNER_TEST_ID = 0
USER_ID = "833031"

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://learning.motion.ac.in/",
    "Origin": "https://learning.motion.ac.in",
}


def upstream_get(url, params):
    try:
        resp = requests.get(url, params=params, headers=REQUEST_HEADERS, timeout=15)
    except requests.exceptions.RequestException as e:
        return False, {"status": 502, "error": f"Network error reaching upstream: {e}"}, 502

    try:
        payload = resp.json()
    except ValueError:
        snippet = resp.text[:300].replace("\n", " ").strip()
        reported_status = resp.status_code if resp.status_code >= 400 else 502
        return False, {
            "status": reported_status,
            "error": f"Upstream returned a non-JSON response (HTTP {resp.status_code}): {snippet or '(empty body)'}",
        }, reported_status

    if resp.status_code >= 400:
        upstream_msg = None
        if isinstance(payload, dict):
            upstream_msg = payload.get("message") or payload.get("error")
        return False, {
            "status": resp.status_code,
            "error": upstream_msg or f"Upstream HTTP {resp.status_code}",
        }, resp.status_code

    return True, payload, resp.status_code


def fetch_all_pages_for_subject(paper_id, subject, user_id):
    all_questions = []
    page = 1
    total_pages = None

    while True:
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
                "questions": [],
                "count": 0,
                "error": data.get("error", "Unknown upstream error"),
            }

        questions_data = data.get("questions", {})
        items = questions_data.get("data", [])
        all_questions.extend(items)

        if total_pages is None:
            total_pages = questions_data.get("last_page", 1)

        if page >= total_pages:
            break

        page += 1

    return {
        "subject": subject,
        "questions": all_questions,
        "count": len(all_questions),
        "total_pages": total_pages,
    }


@app.route("/api")
def proxy_api():
    paper_id = request.args.get("paper_id", DEFAULT_PAPER_ID)
    page = request.args.get("page", 1)
    subject = request.args.get("subject", SUBJECTS[0])
    planner_test_id = request.args.get("planner_test_id", PLANNER_TEST_ID)
    user_id = request.args.get("user_id", USER_ID)

    params = {
        "subject": subject,
        "paper_id": paper_id,
        "planner_test_id": planner_test_id,
        "user_id": user_id,
        "page": page,
    }

    ok, payload, status = upstream_get(EXTERNAL_API, params)
    if not ok:
        return jsonify(payload), status
    return jsonify(payload)


@app.route("/api/solution")
def solution_api():
    qid = request.args.get("qid")
    if not qid:
        return jsonify({"status": 400, "error": "qid is required"}), 400

    subject = request.args.get("subject", SUBJECTS[0])
    paper_id = request.args.get("paper_id", DEFAULT_PAPER_ID)
    planner_test_id = request.args.get("planner_test_id", PLANNER_TEST_ID)
    user_id = request.args.get("user_id", USER_ID)

    params = {
        "subject": subject,
        "paper_id": paper_id,
        "planner_test_id": planner_test_id,
        "user_id": user_id,
        "qid": qid,
    }

    ok, payload, status = upstream_get(SOLUTION_API, params)
    if not ok:
        return jsonify(payload), status
    return jsonify(payload)


@app.route("/api/full_paper")
def full_paper_api():
    paper_id = request.args.get("paper_id", DEFAULT_PAPER_ID)
    user_id = request.args.get("user_id", USER_ID)

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
            except Exception as e:
                results[subject] = {
                    "subject": subject,
                    "questions": [],
                    "count": 0,
                    "error": str(e),
                }

    return jsonify(
        {
            "status": 200,
            "paper_id": paper_id,
            "subjects": results,
        }
    )


HTML_TEMPLATE = """
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
        border-color: var(--green); background: rgba(0,255,65,0.14);
        box-shadow: var(--green-glow); font-weight: 600;
    }
    .opt-box.correct .opt-letter{ color:var(--green); }
    .opt-box.correct::after{
        content:"✔ CORRECT"; margin-left:auto; font-size:0.7em;
        color:var(--green); letter-spacing:1px; white-space:nowrap;
    }

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

    /* ---------- Print-only exam-paper layout ---------- */
    .print-only{ display:none; }

    @media print {
        @page { margin: 18mm 16mm; }
        html, body{ height:auto; }
        body *{ visibility:hidden; }
        #matrixCanvas, .scanlines, .crt-flicker{ display:none !important; }
        #printContainer, #printContainer *{ visibility:visible; }
        #printContainer{
            display:block; position:absolute; inset:0; margin:0; padding:0;
            background:#ffffff; color:#1a1a1a;
            font-family: Georgia, 'Times New Roman', serif;
        }
        .pdf-header{
            display:flex; justify-content:space-between; align-items:flex-end;
            border-bottom:3px solid #1a1a1a; padding-bottom:10px; margin-bottom:18px;
        }
        .pdf-header .pdf-brand{ font-size:1.5em; font-weight:700; letter-spacing:2px; font-family: 'Courier New', monospace; }
        .pdf-header .pdf-brand span{ color:#c0392b; }
        .pdf-header .pdf-meta{ text-align:right; font-size:0.85em; color:#444; font-family: Arial, sans-serif; line-height:1.5; }

        .pdf-subject-title{
            font-family: Arial, sans-serif; font-size:1.15em; font-weight:700; text-transform:uppercase;
            letter-spacing:1px; background:#f0f0f0; padding:6px 12px; margin:22px 0 12px 0;
            border-left:4px solid #1a1a1a;
        }
        .pdf-question{ margin-bottom:18px; page-break-inside: avoid; }
        .pdf-question .pdf-qnum{ font-weight:700; font-size:1.02em; }
        .pdf-question .pdf-qtag{
            font-family: Arial, sans-serif; font-size:0.7em; color:#666; border:1px solid #999;
            border-radius:10px; padding:1px 8px; margin-left:8px; text-transform:uppercase;
        }
        .pdf-question .pdf-qtext{ margin:6px 0 10px 0; line-height:1.5; }
        .pdf-question .pdf-qtext p{ margin:0.4em 0; }
        .pdf-question img{ max-width:100%; height:auto; min-height: 20px; }

        .pdf-opts{ display:grid; grid-template-columns: 1fr 1fr; gap:6px 18px; margin-left:6px; }
        .pdf-opt{
            font-family: Arial, sans-serif; font-size:0.95em; display:flex; gap:6px;
            align-items:flex-start; padding:2px 6px; border-radius:3px;
        }
        .pdf-opt .pdf-opt-letter{ font-weight:700; min-width:16px; }
        .pdf-opt img{ max-width:100%; height:auto; min-height: 20px; }

        .pdf-opt.pdf-correct{
            font-weight:700; background:#e8f7ec; border:1px solid #1e8a3c; color:#0f4d1f;
        }
        .pdf-opt.pdf-correct .pdf-opt-letter::after{ content:" \\2713"; color:#1e8a3c; }

        .pdf-opt-unavailable{
            font-family: Arial, sans-serif; font-size:0.8em; color:#888;
            font-style:italic; margin-left:6px;
        }

        .pdf-answer-key{ margin-top:30px; page-break-before: always; }
        .pdf-answer-key h2{
            font-family: Arial, sans-serif; font-size:1.2em; text-transform:uppercase; letter-spacing:1px;
            border-bottom:2px solid #1a1a1a; padding-bottom:6px; margin-bottom:14px;
        }
        .pdf-key-grid{
            display:grid; grid-template-columns: repeat(6, 1fr); gap:8px 4px;
            font-family: Arial, sans-serif; font-size:0.9em;
        }
        .pdf-key-cell{ border:1px solid #ccc; border-radius:4px; padding:4px 6px; text-align:center; }
        .pdf-key-cell b{ color:#c0392b; }
        .pdf-key-subject-label{
            grid-column: 1 / -1; font-weight:700; font-size:0.85em; text-transform:uppercase;
            color:#444; margin-top:8px;
        }
        .pdf-footer{
            margin-top:20px; padding-top:8px; border-top:1px solid #ccc;
            font-family: Arial, sans-serif; font-size:0.7em; color:#999; text-align:center;
        }
    }
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
            <input type="number" id="paperId" value="{{ default_paper }}" min="1">
            <label for="userId">User_ID ::</label>
            <input type="text" id="userId" value="{{ default_user_id }}" placeholder="0000">
            <button id="goBtn">&gt; Execute</button>
            <button id="resetBtn" class="secondary">&gt; Reset</button>
            <button id="pdfBtn" class="pdf-btn">&gt; Download PDF</button>

            <div class="mode-toggle">
                <span style="margin-right: 6px; color:var(--green-dim); text-transform:uppercase; font-size:0.85em; letter-spacing:1px;">Mode:</span>
                <label><input type="radio" name="mode" value="single" checked> Single</label>
                <label><input type="radio" name="mode" value="full"> Full Dump</label>
            </div>
        </div>

        <div id="status" class="status"></div>
        <div id="pdfStatus" class="status hidden"></div>

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

        <div id="fullView" class="hidden">
            <div id="fullContent"></div>
        </div>

        <div class="footer-tag">
            <b>ACCESS GRANTED</b> :: Trace me if you can. || NY CODER © all rights reversed.
        </div>
    </div>
  </div>
</div>

<div id="printContainer" class="print-only"></div>

<script>
window.MathJax = {
    tex: { inlineMath: [['$', '$'], ['\\\\(', '\\\\)']] },
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
        let paperId = parseInt(paperInput.value) || {{ default_paper }};
        let isLoading = false;
        let currentMode = 'single';
        let currentSingleQuestion = null;
        let lastFullPaperData = null;

        function switchMode(mode) {
            currentMode = mode;
            if (mode === 'single') {
                singleView.classList.remove('hidden');
                fullView.classList.add('hidden');
                if (paperId > 0) fetchQuestion(currentPage);
            } else {
                singleView.classList.add('hidden');
                fullView.classList.remove('hidden');
                fetchFullPaper();
            }
        }

        modeRadios.forEach(radio => {
            radio.addEventListener('change', function() {
                if (this.checked) switchMode(this.value);
            });
        });

        function currentUserId() {
            return (userIdInput.value || '').trim() || '0000';
        }

        // Bulletproof URL fixer and lazy-load handler
        function extractFragmentHtml(raw) {
            if (!raw) return '';
            try {
                const doc = new DOMParser().parseFromString(raw, 'text/html');
                const body = doc.body || doc.documentElement;
                if (!body) return raw;

                // 1. Handle lazy-loaded images
                body.querySelectorAll('img').forEach(el => {
                    if (!el.getAttribute('src')) {
                        const lazySrc = el.getAttribute('data-src') || el.getAttribute('data-original') || el.getAttribute('data-lazy-src');
                        if (lazySrc) el.setAttribute('src', lazySrc);
                    }
                });

                // 2. Fix all URLs
                body.querySelectorAll('img[src], source[src], a[href]').forEach(el => {
                    ['src', 'href'].forEach(attr => {
                        const v = el.getAttribute(attr);
                        if (!v) return;
                        
                        if (/^(data:|blob:|mailto:|#)/i.test(v)) return;
                        if (v.startsWith('//')) {
                            el.setAttribute(attr, 'https:' + v);
                            return;
                        }
                        if (/^https?:/i.test(v)) {
                            if (v.startsWith('http://')) el.setAttribute(attr, v.replace('http://', 'https://'));
                            return;
                        }
                        
                        let absoluteUrl;
                        if (v.startsWith('/')) {
                            absoluteUrl = 'https://learning.motion.ac.in' + v;
                        } else {
                            absoluteUrl = 'https://learning.motion.ac.in/motioneducation/' + v;
                        }
                        el.setAttribute(attr, absoluteUrl);
                    });
                    
                    const srcset = el.getAttribute('srcset');
                    if (srcset) {
                        el.setAttribute('srcset', srcset.split(',').map(part => {
                            const bits = part.trim().split(/\s+/);
                            let u = bits[0];
                            if (!/^(data:|blob:|\/\/)/i.test(u)) {
                                if (/^http:/i.test(u)) {
                                    u = u.replace('http://', 'https://');
                                } else if (!/^https:/i.test(u)) {
                                    u = u.startsWith('/') ? 'https://learning.motion.ac.in' + u : 'https://learning.motion.ac.in/motioneducation/' + u;
                                }
                            } else if (u.startsWith('//')) {
                                u = 'https:' + u;
                            }
                            return [u, ...bits.slice(1)].join(' ');
                        }).join(', '));
                    }
                });
                return body.innerHTML;
            } catch (e) {
                return raw;
            }
        }

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
                    paper_id: paperId, page: page, subject: 'Maths', planner_test_id: 0, user_id: currentUserId()
                });
                const data = await fetchJSON(`/api?${params.toString()}`);
                renderSingle(data);
                statusDiv.textContent = 'Loaded';
                statusDiv.className = 'status';
            } catch (err) {
                statusDiv.textContent = `Error: ${err.message}`;
                statusDiv.className = 'status error';
                prevBtn.disabled = true; nextBtn.disabled = true;
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
                prevBtn.disabled = true; nextBtn.disabled = true;
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
                qid: q.qid, subject: q.subject || 'Maths', toughness: q.toughness || '—', q_text: cleanQText
            };

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

        const solutionCache = {};

        function optionLetter(i) { return String.fromCharCode(65 + i); }

        function normaliseOptions(record) {
            if (!record || record.__error) return [];
            const raw = record.option || record.options || [];
            if (!Array.isArray(raw)) return [];
            return raw.map((o) => {
                if (o == null) return { text: '', correct: false };
                let text = '';
                if (typeof o === 'string') text = o;
                else if (typeof o === 'object') {
                    text = o.option ?? o.text ?? o.option_text ?? o.value ?? '';
                    if (text && typeof text === 'object') text = text.html ?? text.text ?? '';
                }
                const correct = Number(o && o.is_correct) === 1 || String(o && o.is_correct) === '1' || o && o.is_correct === true || o && o.correct === true || Number(o && o.correct) === 1;
                return { text: String(text || ''), correct };
            });
        }

        async function fetchSolutionRecord(qid, subject) {
            if (Object.prototype.hasOwnProperty.call(solutionCache, qid)) return solutionCache[qid];
            try {
                const params = new URLSearchParams({
                    subject: subject || 'Maths', paper_id: paperId, planner_test_id: 0, user_id: currentUserId(), qid: qid
                });
                const data = await fetchJSON(`/api/solution?${params.toString()}`);
                const record = (data.data && data.data[0]) || null;
                solutionCache[qid] = record;
                return record;
            } catch (err) {
                const errored = { __error: err.message };
                solutionCache[qid] = errored;
                return errored;
            }
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
            const opts = normaliseOptions(record);
            let html = '';
            if (opts.length) {
                html += '<div class="opt-grid">';
                opts.forEach((opt, i) => {
                    const cls = opt.correct ? ' correct' : '';
                    html += `<div class="opt-box${cls}">
                        <span class="opt-letter">${optionLetter(i)}.</span>
                        <span>${extractFragmentHtml(opt.text) || '<em>(option text unavailable)</em>'}</span>
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
            if (!this.dataset.qid) return;
            fetchSolution(this.dataset.qid, this.dataset.subject, singleSolBtn, singleSolPanel);
        });

        fullContent.addEventListener('click', function(e) {
            const btn = e.target.closest('.sol-btn');
            if (!btn) return;
            fetchSolution(btn.dataset.qid, btn.dataset.subject, btn, btn.nextElementSibling);
        });

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

        function correctLetterFor(record) {
            const opts = normaliseOptions(record);
            if (!opts.length) return null;
            const idx = opts.findIndex(o => o.correct);
            return idx >= 0 ? optionLetter(idx) : null;
        }

        function buildPrintHtml(numbered, meta) {
            let html = '';
            html += `<div class="pdf-header">
                <div class="pdf-brand">NY <span>CODER</span></div>
                <div class="pdf-meta">
                    Paper ID: ${meta.paperId}<br>
                    User: ${meta.userId}<br>
                    Generated: ${meta.date}
                </div>
            </div>`;

            let lastSubject = null;
            numbered.forEach(item => {
                if (item.subject !== lastSubject) {
                    html += `<div class="pdf-subject-title">${item.subject}</div>`;
                    lastSubject = item.subject;
                }
                const record = item.record;
                html += `<div class="pdf-question">`;
                html += `<div><span class="pdf-qnum">Q${item.num}.</span><span class="pdf-qtag">${item.q.toughness || '—'}</span></div>`;
                html += `<div class="pdf-qtext">${extractFragmentHtml(item.q.q_text) || '(empty)'}</div>`;

                const opts = normaliseOptions(record);
                if (opts.length) {
                    html += '<div class="pdf-opts">';
                    opts.forEach((opt, i) => {
                        html += `<div class="pdf-opt${opt.correct ? ' pdf-correct' : ''}">
                            <span class="pdf-opt-letter">${optionLetter(i)}.</span>
                            <span>${extractFragmentHtml(opt.text) || '<em>(option text unavailable)</em>'}</span>
                        </div>`;
                    });
                    html += '</div>';
                } else if (record && record.__error) {
                    html += `<div class="pdf-opt-unavailable">Options/answer unavailable for QID ${item.q.qid}: ${record.__error}</div>`;
                } else {
                    html += `<div class="pdf-opt-unavailable">No options data available for QID ${item.q.qid}.</div>`;
                }
                html += `</div>`;
            });

            html += `<div class="pdf-answer-key"><h2>Answer Key</h2><div class="pdf-key-grid">`;
            lastSubject = null;
            numbered.forEach(item => {
                if (item.subject !== lastSubject) {
                    html += `<div class="pdf-key-subject-label">${item.subject}</div>`;
                    lastSubject = item.subject;
                }
                const letter = correctLetterFor(item.record);
                html += `<div class="pdf-key-cell">Q${item.num}: <b>${letter || '—'}</b></div>`;
            });
            html += `</div></div>`;
            html += `<div class="pdf-footer">NY CODER :: Question Extraction Terminal — Paper ${meta.paperId} — generated ${meta.date}</div>`;
            return html;
        }

        async function handleDownloadPdf() {
            if (pdfBtn.disabled) return;

            let groups = [];
            if (currentMode === 'single') {
                if (!currentSingleQuestion) {
                    pdfStatus.classList.remove('hidden');
                    pdfStatus.className = 'status error';
                    pdfStatus.textContent = 'No question loaded to export yet.';
                    return;
                }
                groups = [{ subject: currentSingleQuestion.subject, questions: [currentSingleQuestion] }];
            } else {
                if (!lastFullPaperData) {
                    pdfStatus.classList.remove('hidden');
                    pdfStatus.className = 'status error';
                    pdfStatus.textContent = 'No full paper loaded to export yet.';
                    return;
                }
                const subjects = lastFullPaperData.subjects || {};
                groups = ['Maths', 'Physics', 'Chemistry']
                    .map(sub => ({ subject: sub, questions: (subjects[sub] && subjects[sub].questions) || [] }))
                    .filter(g => g.questions.length > 0);
            }

            const numbered = [];
            let n = 0;
            groups.forEach(g => g.questions.forEach(q => { n++; numbered.push({ num: n, subject: g.subject, q }); }));

            if (numbered.length === 0) {
                pdfStatus.classList.remove('hidden');
                pdfStatus.className = 'status error';
                pdfStatus.textContent = 'Nothing to export.';
                return;
            }

            const originalBtnText = pdfBtn.textContent;
            pdfBtn.disabled = true;
            pdfBtn.textContent = '> Preparing...';
            pdfStatus.classList.remove('hidden');
            pdfStatus.className = 'status loading';
            pdfStatus.textContent = `Compiling ${numbered.length} question(s) into PDF...`;

            let done = 0;
            await mapWithConcurrency(numbered, 4, async (item) => {
                item.record = await fetchSolutionRecord(item.q.qid, item.subject);
                done++;
                pdfStatus.textContent = `Compiling ${done}/${numbered.length} question(s) into PDF...`;
            });

            printContainer.innerHTML = buildPrintHtml(numbered, {
                paperId: paperId, userId: currentUserId(), date: new Date().toLocaleString()
            });

            pdfStatus.textContent = 'Rendering equations...';

            const finish = () => {
                pdfBtn.disabled = false;
                pdfBtn.textContent = originalBtnText;
                pdfStatus.classList.add('hidden');
                printContainer.style.cssText = '';
            };

            printContainer.style.cssText = 'display:block; position:fixed; left:-9999px; top:0; width:800px; background:#fff;';

            if (window.MathJax && MathJax.typesetPromise) {
                try {
                    await MathJax.typesetPromise([printContainer]);
                } catch (e) {
                    console.warn('MathJax typeset error (PDF):', e);
                }
            }

            const printCss = Array.from(document.querySelectorAll('style')).map(s => s.outerHTML).join('\\n');

            // Inject MathJax AND base URL directly into the standalone HTML sent to Puppeteer
            const standaloneHtml = `<!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <base href="https://learning.motion.ac.in/motioneducation/">
                ${printCss}
                <style>
                  body{ background:#fff; padding:0; margin:0; }
                  .print-only{ display:block !important; }
                  #printContainer{ position:static !important; left:0 !important; width:auto !important; padding:0; margin:0; }
                  img{ max-width:100%; height:auto; min-height: 20px; }
                </style>
                <script>
                  window.MathJax = {
                      tex: { inlineMath: [['$', '$'], ['\\\\(', '\\\\)']] },
                      chtml: { fontURL: 'https://cdn.jsdelivr.net/npm/mathjax@3/es5/output/chtml/fonts/woff-v2' },
                      startup: {
                          pageReady: () => {
                              return MathJax.startup.defaultPageReady().then(() => {
                                  window.__mathjaxDone = true;
                              });
                          }
                      }
                  };
                </script>
                <script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js" id="MathJax-script" async></script>
            </head>
            <body>
                ${printContainer.outerHTML}
                <script>
                    setTimeout(function() {
                        if (!window.__mathjaxDone) window.__mathjaxDone = true;
                    }, 15000);
                </script>
            </body>
            </html>`;

            pdfStatus.textContent = 'Generating PDF on server...';

            try {
                const resp = await fetch('/api/pdf', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ html: standaloneHtml, filename: `NY_CODER_Paper_${paperId}` }),
                });

                if (!resp.ok) {
                    const errJson = await resp.json().catch(() => ({}));
                    throw new Error(errJson.error || `HTTP ${resp.status}`);
                }

                const blob = await resp.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `NY_CODER_Paper_${paperId}.pdf`;
                document.body.appendChild(a);
                a.click();
                a.remove();
                URL.revokeObjectURL(url);
            } catch (err) {
                pdfStatus.className = 'status error';
                pdfStatus.textContent = `PDF generation failed: ${err.message}`;
            } finally {
                finish();
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
                <strong>Paper ID: ${data.paper_id}</strong> &nbsp;|&nbsp;`;
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
            if (currentMode === 'single') fetchQuestion(1);
            else fetchFullPaper();
        }

        goBtn.addEventListener('click', resetAndFetch);
        resetBtn.addEventListener('click', function() {
            paperInput.value = {{ default_paper }};
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
    return render_template_string(HTML_TEMPLATE, default_paper=DEFAULT_PAPER_ID, default_user_id=USER_ID)
