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

const MAX_HTML_BYTES = 4.4 * 1024 * 1024; // Vercel's request-body ceiling is ~4.5 MB
const SITE_REFERER = 'https://learning.motion.ac.in/';

// The HTML we receive is a static snapshot (equations are already rendered by
// the client), so the render page runs with JavaScript OFF and may only make
// ordinary http(s)/data: requests to public hosts. This keeps the endpoint from
// being usable to run scripts or probe internal addresses.
function isBlockedUrl(rawUrl) {
  let u;
  try { u = new URL(rawUrl); } catch { return true; }
  if (u.protocol === 'data:' || u.protocol === 'about:' || u.protocol === 'blob:') return false;
  if (u.protocol !== 'http:' && u.protocol !== 'https:') return true;
  const h = u.hostname.toLowerCase();
  if (h === 'localhost' || h.endsWith('.localhost') || h.endsWith('.internal') || h.endsWith('.local')) return true;
  if (/^\[.*\]$/.test(h)) return true; // IPv6 literals
  const m = /^(\d+)\.(\d+)\.(\d+)\.(\d+)$/.exec(h);
  if (m) {
    const [a, b] = [Number(m[1]), Number(m[2])];
    if (a === 10 || a === 127 || a === 0) return true;
    if (a === 169 && b === 254) return true;
    if (a === 172 && b >= 16 && b <= 31) return true;
    if (a === 192 && b === 168) return true;
  }
  return false;
}

function footerTemplate(label) {
  const safe = String(label || 'NY CODER').replace(/[<>&"']/g, '').slice(0, 80);
  return `<div style="width:100%;padding:0 16mm;font-family:Arial,Helvetica,sans-serif;font-size:8px;color:#777;display:flex;justify-content:space-between;">
    <span>${safe}</span><span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span></div>`;
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
    try { body = JSON.parse(body || '{}'); } catch { body = {}; }
  }

  const { html, filename, footer } = body;
  if (!html || typeof html !== 'string') {
    res.status(400).json({ error: 'html is required' });
    return;
  }
  if (Buffer.byteLength(html, 'utf8') > MAX_HTML_BYTES) {
    res.status(413).json({ error: 'Document too large for a single PDF request' });
    return;
  }

  let browser;
  try {
    const executablePath = await chromium.executablePath();
    browser = await puppeteer.launch({
      args: chromium.args,
      executablePath,
      headless: 'shell',
    });

    const page = await browser.newPage();
    await page.setJavaScriptEnabled(false);
    // Some image hosts reject requests that have no site Referer.
    await page.setExtraHTTPHeaders({ Referer: SITE_REFERER });
    await page.setRequestInterception(true);
    page.on('request', (r) => {
      if (isBlockedUrl(r.url())) r.abort('blockedbyclient').catch(() => {});
      else r.continue().catch(() => {});
    });

    await page.setContent(html, { waitUntil: 'load', timeout: 30000 });

    // Wait for web fonts and every image (success OR failure) to settle.
    await page.evaluate(async () => {
      const withTimeout = (p, ms) => Promise.race([p, new Promise((r) => setTimeout(r, ms))]);
      try { await withTimeout(document.fonts.ready, 8000); } catch (e) {}
      await withTimeout(Promise.all(Array.from(document.images).map((img) =>
        img.complete ? null : new Promise((r) => { img.onload = img.onerror = r; }))), 15000);
    });

    // Images that failed to load become a visible placeholder instead of
    // silently vanishing, and are reported back to the client.
    const brokenImages = await page.evaluate(() => {
      let n = 0;
      document.querySelectorAll('img').forEach((img) => {
        if (!img.complete || img.naturalWidth === 0) {
          n++;
          const ph = document.createElement('span');
          ph.textContent = '[figure could not be loaded]';
          ph.setAttribute('style', 'display:inline-block;padding:2mm 4mm;border:1px dashed #b3261e;color:#b3261e;font:9pt Arial,sans-serif;');
          img.replaceWith(ph);
        }
      });
      return n;
    });

    const pdfArray = await page.pdf({
      format: 'A4',
      printBackground: true,
      preferCSSPageSize: true,
      displayHeaderFooter: true,
      headerTemplate: '<div></div>',
      footerTemplate: footerTemplate(footer),
      margin: { top: '18mm', bottom: '20mm', left: '16mm', right: '16mm' },
    });
    // page.pdf() returns a Uint8Array; wrap in Buffer and use res.end() so raw
    // bytes (not JSON) go out on the wire.
    const pdfBuffer = Buffer.from(pdfArray);

    if (pdfBuffer.length === 0 || pdfBuffer.slice(0, 5).toString('latin1') !== '%PDF-') {
      throw new Error(`page.pdf() did not return valid PDF bytes (length=${pdfBuffer.length})`);
    }

    await browser.close();
    browser = null;

    const safeName = (filename || 'question_paper').replace(/[^a-zA-Z0-9_\-]/g, '_').slice(0, 80);
    res.statusCode = 200;
    res.setHeader('Content-Type', 'application/pdf');
    res.setHeader('Content-Disposition', `attachment; filename="${safeName}.pdf"`);
    res.setHeader('Content-Length', pdfBuffer.length);
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Access-Control-Expose-Headers', 'X-Pdf-Warnings');
    if (brokenImages) res.setHeader('X-Pdf-Warnings', `broken-images=${brokenImages}`);
    res.end(pdfBuffer);
  } catch (err) {
    console.error('PDF generation failed:', err.stack || err);
    if (browser) await browser.close().catch(() => {});
    res.status(500).json({ error: `PDF generation failed: ${err.message}` });
  }
};
