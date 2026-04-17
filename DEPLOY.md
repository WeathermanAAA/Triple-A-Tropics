# Deploying your website — step by step

You're going to do three things:

1. Put all these files in a **GitHub repository**
2. Turn on **GitHub Pages** so the site is live on the internet for free
3. Let the **GitHub Action** (included) regenerate the chart every 6 hours automatically

Total time: about 10 minutes. No command line required — everything can be done in a web browser.

---

## Step 1 — Make a GitHub account (skip if you have one)

Go to [github.com/signup](https://github.com/signup) and create a free account.

## Step 2 — Create a new repository

1. Click the **+** in the top right of GitHub → **New repository**.
2. **Repository name:** pick anything, but a clean choice is **`weathermanaaa`** (or `wp-ace`, or your domain name). Lowercase, no spaces.
3. Set it to **Public**.
4. **Do not** check "Add a README" — leave the repo empty.
5. Click **Create repository**.

GitHub will show a page titled "Quick setup." Leave that tab open — you'll come back in a second.

## Step 3 — Upload the files

On that "Quick setup" page, click the **uploading an existing file** link (it's in a sentence that says "Get started by creating a new file or uploading an existing file").

You need to upload **all of these files** from the folder this README came in:

```
generate_wp_ace_plot.py
index.html
wp_ace.html
wp_ace_data.json
.gitignore
.github/workflows/update-ace.yml
```

A few tips for uploading:

- You can drag the whole folder onto the browser window — GitHub will unpack it and preserve the `.github/workflows/` subfolder. If it doesn't, upload that file by itself afterward: create a new file named `.github/workflows/update-ace.yml` and paste its contents.
- **Do not** upload `ibtracs.WP.list.v04r01.csv` — it's huge, and the automation will download a fresh copy each run. (The included `.gitignore` already tells Git to skip it.)

After the files are staged, scroll down and click **Commit changes**.

## Step 4 — Turn on GitHub Pages

1. In your repository, click the **Settings** tab.
2. In the left sidebar, click **Pages**.
3. Under "Build and deployment":
   - **Source:** Deploy from a branch
   - **Branch:** `main` / **root** (`/`)
   - Click **Save**.
4. Wait about 30–60 seconds, then reload. You'll see a green box:

   > Your site is live at **https://yourusername.github.io/weathermanaaa/**

That's your URL. Open it. You should see the site with the live chart.

## Step 5 — Turn on the auto-updater

The chart will now regenerate itself every 6 hours, but you want to make sure it's actually running:

1. In your repo, click the **Actions** tab.
2. If GitHub asks you to enable Actions, click **I understand my workflows, go ahead and enable them**.
3. You should see a workflow called **Update WP ACE chart** in the left sidebar. Click it.
4. Click the **Run workflow** button on the right → **Run workflow** (green button) to trigger it once right now.
5. Wait ~2 minutes. It should finish with a green check mark and commit a fresh `wp_ace.html` to your repo.
6. Refresh your site — the "last updated" timestamp in the chart should reflect the new run.

From now on it'll run automatically at 00:15, 06:15, 12:15, and 18:15 UTC every day, and GitHub Pages will publish the fresh HTML within a minute of each run.

---

## Using your own domain name (optional)

If you own something like `weathermanaaa.com` and want the site to live there instead of `github.io`:

1. In your repo → **Settings → Pages**, under "Custom domain," enter your domain and save.
2. At your domain registrar (Namecheap, GoDaddy, Cloudflare, etc.), add these DNS records:
   - Four **A** records pointing `@` (the root) to:
     - `185.199.108.153`
     - `185.199.109.153`
     - `185.199.110.153`
     - `185.199.111.153`
   - One **CNAME** record pointing `www` to `yourusername.github.io`
3. Wait a few minutes, then check **Enforce HTTPS** back in GitHub Pages settings.

Full instructions: [GitHub Pages — custom domain docs](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site).

---

## Editing the site

- Change anything about the landing page? Edit `index.html` directly on GitHub (click the file, click the pencil icon, commit). GitHub Pages will republish in under a minute.
- Change the chart style, colors, or computation? Edit `generate_wp_ace_plot.py` the same way. Then go to **Actions → Update WP ACE chart → Run workflow** to regenerate immediately instead of waiting for the next scheduled run.
- Add more pages? Just add more `.html` files alongside `index.html` and link to them.

---

## What each file does

| File | Purpose |
| --- | --- |
| `index.html` | Your homepage — the page visitors land on |
| `wp_ace.html` | The live chart (regenerated automatically; embedded into index.html via iframe) |
| `wp_ace_data.json` | The processed numbers behind the chart (useful if you want to build more charts later) |
| `generate_wp_ace_plot.py` | The script that builds `wp_ace.html` from IBTrACS data |
| `.github/workflows/update-ace.yml` | The automation: downloads fresh data and reruns the script every 6 hours |
| `.gitignore` | Tells Git to skip large/temporary files |

---

## Troubleshooting

**"Your site is published at ..." but the page is 404.**
Wait another minute and hard-refresh (Ctrl/Cmd+Shift+R). GitHub Pages can take 60-90 seconds on the first deploy.

**The chart shows but looks broken.**
Open the site, right-click → View Page Source, and make sure the `<iframe src="wp_ace.html">` is there. If `wp_ace.html` is missing from your repo, upload it.

**GitHub Action fails with a network error.**
Re-run it (Actions → the failed run → Re-run all jobs). The NCEI data server occasionally rate-limits; the action retries three times automatically but sometimes needs another try.

**I want to change how often it runs.**
Edit `.github/workflows/update-ace.yml` — look for the `cron:` line. The syntax is `minute hour day month weekday`. Some handy options:
- `"0 */3 * * *"` — every 3 hours
- `"0 0 * * *"` — once a day at midnight UTC
- `"0 0,12 * * *"` — twice a day (midnight and noon UTC)

---

Questions or something broke? Make a note of what you saw and I can help you fix it.
