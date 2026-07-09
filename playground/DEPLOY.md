# Deploying the Catalyst playground to Vercel

The app is split so it works both locally (a Python `http.server` in `app.py`) and
on Vercel (a dependency-free serverless function in `api/chat.py`). Static assets
live in `web/`; `vercel.json` serves them and routes `/api/chat` to the function.

## 1. Rotate your API keys first
The keys used during development were pasted in chat and are compromised.
**Generate new ones** (console.anthropic.com / platform.openai.com) — they go into
Vercel as environment variables, never into the repo.

## 2. Push to GitHub (private)
`gh` isn't installed here, so create the repo and push from your machine:

```bash
cd ~/catalyst-workspace/catalyst
# create a PRIVATE repo named "catalyst" on github.com/devadhathan, then:
git branch -M main
git remote add origin https://github.com/devadhathan/catalyst.git
git push -u origin main
```

(or `gh repo create devadhathan/catalyst --private --source=. --push` if you install the GitHub CLI.)

## 3. Import into Vercel
1. vercel.com → **Add New → Project** → import `devadhathan/catalyst`.
2. **Root Directory: `playground`** ← important (the app lives in the `playground/` subfolder).
3. Framework preset: **Other**. Leave build/output empty.
4. **Environment Variables** — add one of:
   - `ANTHROPIC_API_KEY` = your new key  (uses Claude Haiku — cheapest)
   - or `OPENAI_API_KEY` = your new key  (uses gpt-4o-mini)
   - optional: `PROVIDER` (`anthropic`|`openai`), `ANTHROPIC_SMART_MODEL`, `OPENAI_SMART_MODEL`, etc.
5. **Deploy.**

## Notes
- The function calls the model APIs over plain HTTPS (stdlib `urllib`) — no `requirements.txt`,
  tiny cold starts.
- Model calls take ~7–15s; `vercel.json` sets `maxDuration: 60`. If a complex dashboard still
  times out on the Hobby plan, use the fast model only or upgrade the plan.
- The Polaris bundle (`web/polaris-bundle.js/css`) is committed prebuilt, so no Node build runs
  on Vercel. If you change `polaris-src/`, rebuild locally (`cd polaris-src && npm run build`)
  and commit the updated bundle.
