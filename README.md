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

## Notes
- All three routes (`/`, `/api`, `/api/full_paper`) are served by the single function in `api/index.py`.
- `DEFAULT_PAPER_ID` and other constants are set at the top of `api/index.py`.
- Vercel serverless functions have a request timeout (10s on the free Hobby plan). `full_paper_api` fetches three subjects in parallel threads, but if a paper has a lot of pages per subject it can still be slow — consider upgrading the plan or trimming `PLANNER_TEST_ID`/paging logic if you hit timeouts.
