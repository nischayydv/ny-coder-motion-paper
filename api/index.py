#!/usr/bin/env python3
"""
NY CODER :: Question Viewer — Vercel serverless entrypoint.
Flask app exposed as `app`; Vercel's @vercel/python runtime auto-detects
and serves any WSGI-compatible `app` object from api/index.py.

Local dev:  vercel dev
Deploy:     vercel --prod
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
USER_ID = "0000"


# ---------- Helper: fetch all pages for one subject ----------
def fetch_all_pages_for_subject(paper_id, subject):
    all_questions = []
    page = 1
    total_pages = None

    while True:
        params = {
            "subject": subject,
            "paper_id": paper_id,
            "planner_test_id": PLANNER_TEST_ID,
            "user_id": USER_ID,
            "page": page,
        }
        try:
            resp = requests.get(EXTERNAL_API, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            return {
                "subject": subject,
                "questions": [],
                "count": 0,
                "error": str(e),
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


# ---------- Proxy endpoint for single question (legacy) ----------
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

    try:
        resp = requests.get(EXTERNAL_API, params=params, timeout=10)
        resp.raise_for_status()
        return jsonify(resp.json())
    except Exception as e:
        return jsonify({"status": 500, "error": str(e)}), 500


# ---------- Proxy endpoint for a question's solution ----------
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

    try:
        resp = requests.get(SOLUTION_API, params=params, timeout=10)
        resp.raise_for_status()
        return jsonify(resp.json())
    except Exception as e:
        return jsonify({"status": 500, "error": str(e)}), 500


# ---------- Full paper (all subjects, all pages) ----------
@app.route("/api/full_paper")
def full_paper_api():
    paper_id = request.args.get("paper_id", DEFAULT_PAPER_ID)

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        future_to_subject = {
            executor.submit(fetch_all_pages_for_subject, paper_id, subject): subject
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


# ---------- HTML page (embedded, NY CODER theme) ----------
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
    .controls input[type="number"] {
        padding: 9px 12px; width: 150px; background:#02120a; color:var(--green);
        border: 1px solid var(--border); border-radius: 4px; font-size: 1em; font-family:inherit;
        box-shadow: inset 0 0 8px rgba(0,255,65,0.15);
    }
    .controls input[type="number"]:focus{ outline:none; border-color:var(--green); box-shadow: var(--green-glow); }

    .controls button {
        padding: 9px 20px; background: transparent; color: var(--green); border: 1px solid var(--green);
        border-radius: 4px; font-weight: 600; cursor: pointer; transition: 0.15s; font-family:inherit;
        letter-spacing:1px; text-transform:uppercase; font-size:0.85em; position:relative; overflow:hidden;
    }
    .controls button:hover { background: var(--green); color:#001a05; box-shadow: var(--green-glow); }
    .controls button:active{ transform: scale(0.96); }
    .controls button.secondary { border-color: var(--amber); color: var(--amber); }
    .controls button.secondary:hover { background: var(--amber); color:#231800; box-shadow:0 0 14px var(--amber); }

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
            <button id="goBtn">&gt; Execute</button>
            <button id="resetBtn" class="secondary">&gt; Reset</button>

            <div class="mode-toggle">
                <span style="margin-right: 6px; color:var(--green-dim); text-transform:uppercase; font-size:0.85em; letter-spacing:1px;">Mode:</span>
                <label><input type="radio" name="mode" value="single" checked> Single</label>
                <label><input type="radio" name="mode" value="full"> Full Dump</label>
            </div>
        </div>

        <div id="status" class="status"></div>

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

<script>
window.MathJax = {
    tex: { inlineMath: [['$', '$'], ['\\(', '\\)']] },
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

        const singleView = document.getElementById('singleView');
        const fullView = document.getElementById('fullView');
        const fullContent = document.getElementById('fullContent');

        const modeRadios = document.querySelectorAll('input[name="mode"]');

        let currentPage = 1;
        let totalPages = 0;
        let paperId = parseInt(paperInput.value) || {{ default_paper }};
        let isLoading = false;
        let currentMode = 'single';

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
                    user_id: '0000'
                });
                const url = `/api?${params.toString()}`;
                const response = await fetch(url);
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                const data = await response.json();
                if (data.status !== 200) {
                    throw new Error(data.error || 'API error');
                }
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
                return;
            }

            const q = items[0];
            qidDisplay.textContent = `QID: ${q.qid}`;
            toughnessDisplay.textContent = q.toughness || '—';
            qTextDisplay.innerHTML = q.q_text || '(empty)';

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

        // ---------- Solution feature (shared by single + full views) ----------
        const solutionCache = {}; // qid -> parsed solution record

        function optionLetter(i) {
            return String.fromCharCode(65 + i); // 0 -> A, 1 -> B, ...
        }

        function renderSolutionPanel(panelEl, record) {
            if (!record) {
                panelEl.innerHTML = '<div class="sol-error">No solution data available.</div>';
                return;
            }
            const opts = record.option || [];
            let html = '';
            if (opts.length) {
                html += '<div class="opt-grid">';
                opts.forEach((opt, i) => {
                    const correct = Number(opt.is_correct) === 1;
                    html += `<div class="opt-box${correct ? ' correct' : ''}">
                        <span class="opt-letter">${optionLetter(i)}.</span>
                        <span>${opt.option || ''}</span>
                    </div>`;
                });
                html += '</div>';
            }
            html += '<div class="sol-heading">Solution</div>';
            html += `<div class="sol-text">${record.sol_text || 'No written solution available for this question.'}</div>`;
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

            if (solutionCache[qid]) {
                renderSolutionPanel(panelEl, solutionCache[qid]);
                return;
            }

            panelEl.innerHTML = '<div class="sol-loading">decrypting solution payload</div>';

            try {
                const params = new URLSearchParams({
                    subject: subject || 'Maths',
                    paper_id: paperId,
                    planner_test_id: 0,
                    user_id: '0000',
                    qid: qid
                });
                const url = `/api/solution?${params.toString()}`;
                const response = await fetch(url);
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                const data = await response.json();
                if (data.status !== 200) {
                    throw new Error(data.error || data.message || 'API error');
                }
                const record = (data.data && data.data[0]) || null;
                solutionCache[qid] = record;
                renderSolutionPanel(panelEl, record);
            } catch (err) {
                panelEl.innerHTML = `<div class="sol-error">Error: ${err.message}</div>`;
            }
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

        async function fetchFullPaper() {
            if (isLoading) return;
            isLoading = true;
            statusDiv.textContent = 'Loading full paper...';
            statusDiv.className = 'status loading';

            try {
                const params = new URLSearchParams({ paper_id: paperId });
                const url = `/api/full_paper?${params.toString()}`;
                const response = await fetch(url);
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                const data = await response.json();
                if (data.status !== 200) {
                    throw new Error(data.error || 'API error');
                }
                renderFull(data);
                statusDiv.textContent = 'Full paper loaded';
                statusDiv.className = 'status';
            } catch (err) {
                statusDiv.textContent = `Error: ${err.message}`;
                statusDiv.className = 'status error';
                fullContent.innerHTML = '<p style="color:#ff2d55;">Failed to load full paper.</p>';
            } finally {
                isLoading = false;
            }
        }

        function renderFull(data) {
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
                    html += `<p style="color:#3a5c3e;padding:10px 0;">No questions found for this subject.</p>`;
                } else {
                    for (const q of questions) {
                        html += `<div class="question-item">`;
                        html += `<div class="qid">QID: ${q.qid}</div>`;
                        html += `<div class="toughness">${q.toughness || '—'}</div>`;
                        html += `<div class="q-text">${q.q_text || '(empty)'}</div>`;
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
    return render_template_string(HTML_TEMPLATE, default_paper=DEFAULT_PAPER_ID)


# Note: no app.run() here — Vercel's @vercel/python runtime imports this
# module and calls the WSGI `app` object directly per-request. Keep this
# file's top-level `app` name unchanged.
