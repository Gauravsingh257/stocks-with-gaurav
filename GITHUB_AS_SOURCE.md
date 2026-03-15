# Using GitHub as the Single Source (No “Local First” Required)

Your **Railway** (backend) and **Vercel** (frontend) already deploy **from GitHub**. You do not sync code to Railway or Vercel manually — you push to GitHub and they build from the repo.

---

## How It Works Today

| Step | What happens |
|------|-------------------------------|
| 1. You edit code | Locally in `Trading Algo` folder (or on GitHub) |
| 2. You push to GitHub | `git push origin main` (or run `sync.bat`) |
| 3. Railway & Vercel | **Auto-deploy** from the GitHub repo (no extra “sync to cloud” step) |

So the flow is: **GitHub is the source → Railway and Vercel pull from GitHub.**  
Your local folder is just one place to make changes that you then push to GitHub.

---

## Option 1: Keep Local Editing, But Think “GitHub First”

- **Single source of truth:** the `main` branch on GitHub.
- **Workflow:** Edit locally → commit → `git push origin main` (or `sync.bat`).
- **Deployments:** Railway and Vercel deploy automatically when you push to `main`.
- You are not “syncing to Railway/Vercel”; you are **pushing to GitHub**, and they deploy from there.

No change to your tools — just the mental model: **code lives on GitHub; local is where you edit before pushing.**

---

## Option 2: Edit Directly on GitHub (No Local Code Required)

If you want to **use GitHub instead of local** for editing:

### A. GitHub Web Editor

1. Open your repo on GitHub.
2. Open a file → click the **pencil (Edit)** icon.
3. Edit, commit (e.g. “Update config”), choose **Commit directly to `main`** (or create a branch and merge).
4. Railway and Vercel will auto-deploy from the new commit.

Good for: small fixes, config tweaks, README/docs.

### B. GitHub Codespaces (Full IDE in the Browser)

1. In your repo, click **Code** → **Codespaces** → **Create codespace on main**.
2. Edit in a full VS Code–like environment in the browser.
3. Commit and push from the terminal in the Codespace:  
   `git add -A && git commit -m "Your message" && git push origin main`
4. Railway and Vercel auto-deploy as above.

Good for: real development without installing anything on your PC.

---

## What Still Stays Local or Manual

These are **not** in GitHub (or shouldn’t be) and still need a local or manual step:

| Item | Why | What you do |
|------|-----|-------------|
| **Trade ledger** | CSV is local; production DB is on Railway | Run `sync_trades_to_cloud.ps1` to push trades to the backend API |
| **Kite token** | Secret, expires daily | Run `go_live.bat` (or update `KITE_ACCESS_TOKEN` in Railway manually) |
| **Secrets** | `.go_live_config`, API keys | Keep local / in env vars; never commit to GitHub |

So: **code** can be “GitHub only”; **trades and live credentials** still go from your machine or config to the services.

---

## One-Line Summary

- **Code:** Edit locally **or** on GitHub → push to `main` → Railway and Vercel deploy from GitHub. No separate “sync to Railway/Vercel” step.
- **Trades:** Run `sync_trades_to_cloud.ps1` when you want the site to have your latest trade log.
- **Kite:** Use `go_live.bat` or update Railway variables so the site and engine can connect to Zerodha.
