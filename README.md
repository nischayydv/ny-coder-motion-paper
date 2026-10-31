# NY CODER :: Question Viewer (Vercel)

Flask app, deployed as a Vercel serverless function.

## Structure
```
nycoder-vercel/
├── api/
│   └── index.py      # Flask app (routes: /, /api, /api/full_paper)
├── vercel.json        # routes everything to api/index.py
├── requirements.txt   # Flask + requests
└── README.md
```

## Deploy

1. Install the Vercel CLI (once):
   ```
   npm i -g vercel
   ```
2. From inside this folder:
   ```
   vercel
   ```
   Follow the prompts (link/create a project). This deploys a preview.
3. Ship to production:
   ```
   vercel --prod
   ```

Or push this folder to a GitHub repo and import it in the Vercel dashboard (New Project → Import Git Repository) — it auto-detects `vercel.json` and the Python runtime.

## Local test before deploying
```
vercel dev
```
This runs the same serverless routing locally.

## Configuration (optional environment variables)
| Variable | Purpose | Default |
|---|---|---|
| `NYCODER_USER_ID` | Upstream user id pre-filled in the UI | `833031` |
| `NYCODER_DEFAULT_PAPER_ID` | Paper id pre-filled in the UI | `46921` |

Set them in Vercel → Project → Settings → Environment Variables.

## PDF export — what it guarantees
- Correct option(s): green card, filled letter badge, bold text and a tick (readable in B/W too). Multi-correct questions list every correct letter in the answer key; integer-type questions show their answer.
- Blanks are always visible: `____` runs, empty `<u>`/underlined/bordered spans, `<input>` boxes, `\underline{\hspace{..}}` and empty `\underline{}` all print as a ruled blank.
- Upstream HTML is sanitised: scripts, `<style>`, classes, ids and event handlers removed; invisible-text styles (white colour, `visibility`, `opacity`) stripped; relative image URLs made absolute.
- Option fetches retry with back-off and failures are never cached. If options still cannot be loaded you are asked before the PDF is produced, and the PDF marks those questions. A partially-loaded subject blocks export instead of silently shipping an incomplete paper.
- Images that fail to load print as a visible `[figure could not be loaded]` box and are reported after download.
- The render page runs with JavaScript disabled and blocks requests to private/loopback/metadata addresses.

## Notes
- Function timeouts depend on your plan / Fluid Compute settings; a full 75-question paper typically needs 20-40 s.
- The PDF request body is limited to ~4.4 MB (Vercel limit); the UI tells you if a paper exceeds it.
- Consider enabling Vercel's WAF / rate limiting on `/api/pdf` since it launches a headless browser per request.
