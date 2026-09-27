// api/pdf.js
// Node serverless function, sits alongside api/index.py (Python).
// Vercel builds each entry in vercel.json's "builds" with its matching
// runtime, so this needs an explicit @vercel/node build entry (see
// vercel.json) — it won't be picked up automatically the way it would
// be with zero-config Vercel projects.
//
// POST body: { html: "<standalone HTML string>", filename: "..." }
// Returns: application/pdf, Content-Disposition: attachment

const chromium = require('@sparticuz/chromium');
const puppeteer = require('puppeteer-core');

module.exports = async (req, res) => {
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
    browser = await puppeteer.launch({
      args: chromium.args,
      executablePath: await chromium.executablePath(),
      headless: chromium.headless,
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
    if (browser) await browser.close().catch(() => {});
    res.status(500).json({ error: `PDF generation failed: ${err.message}` });
  }
};
