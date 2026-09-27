// api/pdf.js
// Node serverless function, sits alongside api/index.py (Python).
// Vercel builds each entry in vercel.json's "builds" with its matching
// runtime, so this needs an explicit @vercel/node build entry (see
// vercel.json) — it won't be picked up automatically the way it would
// be with zero-config Vercel projects. vercel.json also needs:
//   "config": { "includeFiles": "node_modules/@sparticuz/chromium/**" }
// on this build entry — without it, @vercel/node's automatic dependency
// tracing (@vercel/nft) frequently drops @sparticuz/chromium's bundled
// binary, because it's loaded via a dynamic file-path lookup rather than
// a plain require(). That produces exactly the failure this file guards
// against below: an instant crash with no useful error surfaced.
//
// POST body: { html: "<standalone HTML string>", filename: "..." }
// Returns: application/pdf, Content-Disposition: attachment
//
// GET ?selfcheck=1 — quick diagnostic: resolves chromium's executable
// path without launching a full browser or rendering anything. Hit this
// first when debugging a 500 here; it isolates "chromium isn't bundled
// right" from "something broke during the actual PDF render".
//
// Version note: package.json pins @sparticuz/chromium to ^141.0.0 and
// puppeteer-core to ^24.26.1. Older @sparticuz/chromium releases only
// detected a "real" AWS Lambda environment (via AWS-specific env vars)
// to decide whether to extract their bundled AL2023 shared-library set
// (which includes libnss3.so). Vercel's Fluid Compute runtime is
// Lambda-compatible but doesn't set those AWS env vars, so older
// versions silently skipped extraction here and Chromium failed to
// launch with "libnss3.so: cannot open shared object file". 141.x+
// detects Vercel natively via the VERCEL env var Vercel always sets.
// Don't downgrade below ~131 without re-checking this.

let chromium, puppeteer, loadError;
try {
  chromium = require('@sparticuz/chromium');
  puppeteer = require('puppeteer-core');
} catch (err) {
  // A require() failure here (missing/misbundled dependency) happens at
  // module load time, before our handler below ever runs — which is why
  // it previously showed up as a bare 500 with no JSON body and no
  // console output. Capturing it here instead lets every request return
  // a real error message.
  loadError = err;
}

module.exports = async (req, res) => {
  if (loadError) {
    console.error('api/pdf.js failed to load dependencies:', loadError.stack || loadError);
    res.status(500).json({
      error: `Server misconfigured: failed to load chromium/puppeteer (${loadError.message}). Check vercel.json's includeFiles for api/pdf.js.`,
    });
    return;
  }

  if (req.method === 'GET' && req.query && req.query.selfcheck) {
    try {
      const execPath = await chromium.executablePath();
      res.status(200).json({ ok: true, executablePath: execPath });
    } catch (err) {
      console.error('selfcheck failed:', err.stack || err);
      res.status(500).json({ ok: false, error: err.message });
    }
    return;
  }

  if (req.method !== 'POST') {
    res.status(405).json({ error: 'POST only' });
    return;
  }

  let body = req.body;
  if (!body || typeof body === 'string') {
    try {
      body = JSON.parse(body || '{}');
    } catch {
      body = {};
    }
  }

  const { html, filename } = body;
  if (!html) {
    res.status(400).json({ error: 'html is required' });
    return;
  }

  let browser;
  try {
    const executablePath = await chromium.executablePath();
    browser = await puppeteer.launch({
      args: chromium.args,
      executablePath,
    });

    const page = await browser.newPage();
    await page.setContent(html, { waitUntil: 'networkidle0', timeout: 30000 });
    await page.waitForFunction('window.__mathjaxDone === true', { timeout: 15000 }).catch(() => {});

    const pdfBuffer = await page.pdf({
      format: 'A4',
      printBackground: true,
      margin: { top: '18mm', bottom: '18mm', left: '16mm', right: '16mm' },
    });

    await browser.close();

    const safeName = (filename || 'question_paper').replace(/[^a-zA-Z0-9_\-]/g, '_');
    res.setHeader('Content-Type', 'application/pdf');
    res.setHeader('Content-Disposition', `attachment; filename="${safeName}.pdf"`);
    res.status(200).send(pdfBuffer);
  } catch (err) {
    console.error('PDF generation failed:', err.stack || err);
    if (browser) await browser.close().catch(() => {});
    res.status(500).json({ error: `PDF generation failed: ${err.message}` });
  }
};
