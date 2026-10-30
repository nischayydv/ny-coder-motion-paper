// api/pdf.js
// Node serverless function that renders the standalone HTML produced by
// the client into a real PDF using headless Chromium.

const MAX_HTML_BYTES = 8 * 1024 * 1024; // 8 MB payload guard

let chromium, puppeteer, loadError;
try {
  chromium = require('@sparticuz/chromium');
  puppeteer = require('puppeteer-core');
} catch (err) {
  loadError = err;
}

const UPSTREAM_ORIGIN = 'https://learning.motion.ac.in';
const BROWSER_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';

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

  const { html, filename } = body || {};
  if (!html || typeof html !== 'string') {
    res.status(400).json({ error: 'html is required (non-empty string)' });
    return;
  }
  if (Buffer.byteLength(html, 'utf8') > MAX_HTML_BYTES) {
    res.status(413).json({ error: `html payload too large (limit ${MAX_HTML_BYTES} bytes)` });
    return;
  }

  let browser;
  try {
    const executablePath = await chromium.executablePath();
    browser = await puppeteer.launch({
      args: [...chromium.args, '--disable-dev-shm-usage', '--font-render-hinting=none'],
      executablePath,
      headless: chromium.headless,
      defaultViewport: { width: 1024, height: 1400 },
    });

    const page = await browser.newPage();
    page.on('dialog', (d) => d.dismiss().catch(() => {}));
    page.on('pageerror', (e) => console.warn('page error:', e.message));

    await page.setExtraHTTPHeaders({
      Referer: `${UPSTREAM_ORIGIN}/`,
      Origin: UPSTREAM_ORIGIN,
      'User-Agent': BROWSER_UA,
      Accept: 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
    });
    await page.setUserAgent(BROWSER_UA);

    await page.setRequestInterception(true);
    page.on('request', (request) => {
      try {
        const url = request.url();
        if (url.startsWith('data:') || url.startsWith('blob:') || url.startsWith('chrome')) {
          request.continue(); return;
        }
        if (url.startsWith('about:blank/')) {
          request.continue({ url: UPSTREAM_ORIGIN + url.slice('about:blank'.length) }); return;
        }
        if (url.startsWith('/')) {
          request.continue({ url: UPSTREAM_ORIGIN + url }); return;
        }
        request.continue();
      } catch {
        try { request.continue(); } catch {}
      }
    });

    await page.setContent(html, { waitUntil: 'domcontentloaded', timeout: 30000 });

    // Wait for MathJax to complete typesetting
    await page.waitForFunction('window.__mathjaxDone === true', { timeout: 20000, polling: 100 }).catch((e) => console.warn('mathjax wait timed out (continuing):', e.message));

    // Wait for all images to load (or error out)
    await page.evaluate(async () => {
        const imgs = Array.from(document.images);
        await Promise.all(imgs.map((img) => {
            if (img.complete && img.naturalWidth > 0) return Promise.resolve();
            return new Promise((resolve) => {
                const done = () => resolve();
                img.addEventListener('load', done, { once: true });
                img.addEventListener('error', done, { once: true });
                setTimeout(done, 8000);
            });
        }));
        if (document.fonts && document.fonts.ready) await document.fonts.ready;
    }).catch((e) => console.warn('asset wait error (continuing):', e.message));

    await new Promise((r) => setTimeout(r, 150));

    const pdfArray = await page.pdf({
      format: 'A4',
      printBackground: true,
      preferCSSPageSize: true,
      margin: { top: '18mm', bottom: '18mm', left: '16mm', right: '16mm' },
    });

    const pdfBuffer = Buffer.from(pdfArray);

    if (pdfBuffer.length === 0 || pdfBuffer.slice(0, 5).toString('latin1') !== '%PDF-') {
      throw new Error(`page.pdf() did not return valid PDF bytes (length=${pdfBuffer.length})`);
    }

    await browser.close();
    browser = null;

    const safeName = (filename || 'question_paper').replace(/[^a-zA-Z0-9_\-]/g, '_');
    res.statusCode = 200;
    res.setHeader('Content-Type', 'application/pdf');
    res.setHeader('Content-Disposition', `attachment; filename="${safeName}.pdf"`);
    res.setHeader('Content-Length', pdfBuffer.length);
    res.setHeader('Cache-Control', 'no-store');
    res.end(pdfBuffer);
  } catch (err) {
    console.error('PDF generation failed:', err.stack || err);
    if (browser) await browser.close().catch(() => {});
    try {
      res.status(500).json({ error: `PDF generation failed: ${err.message}` });
    } catch {}
  }
};
