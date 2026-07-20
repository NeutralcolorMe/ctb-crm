# Instagram feed pipeline

The website's Instagram band (homepage of ctbrealtysg.com) reads
`instagram/instagram.json` from this repo's GitHub Pages site:

```
https://neutralcolorme.github.io/ctb-crm/instagram/instagram.json
```

`.github/workflows/instagram-sync.yml` refreshes it daily (09:17 SGT) via the
official **Instagram API with Instagram Login** — no scraping. Post images are
self-hosted under `instagram/media/` so nothing breaks when Instagram's CDN
URLs expire. Until the token below is configured, the workflow exits quietly
and the website shows a "Follow @ctb.realty" CTA instead of posts — it starts
showing the real feed automatically on the first successful sync.

## One-time setup (~15 minutes)

Prerequisite: the `ctb.realty` Instagram account must be a **professional
account** (Business or Creator — free, switchable in the Instagram app under
Settings → Account type and tools).

1. Go to https://developers.facebook.com/ and log in with the Facebook/Meta
   account that manages CTB (create a developer account if prompted).
2. **Create app** → use case: **"Manage everything on your Page"** is NOT
   needed — choose **Other → Business**, name it e.g. `CTB Website Feed`.
3. In the app dashboard, add the product **"Instagram" → API setup with
   Instagram login**.
4. Under **Generate access tokens**, click **Add account** and log in as
   `ctb.realty`, granting the requested permissions
   (`instagram_business_basic` is the only one needed).
5. Click **Generate token** next to the connected account, copy the token
   (long string starting with `IG…`). This is a **long-lived token (60 days)**.
6. In GitHub: `ctb-crm` repo → Settings → Secrets and variables → Actions →
   **New repository secret**:
   - Name: `IG_ACCESS_TOKEN`
   - Value: the token from step 5
7. (Recommended) Add a second secret `GH_PAT` — a fine-grained personal access
   token for this repo with **Secrets: read/write** permission. The workflow
   uses it to store the auto-refreshed Instagram token every run, so the
   connection never expires. Without it, you must repeat steps 4–6 every
   ~60 days (the workflow warns when refresh persistence is unavailable).
8. Trigger the first run: repo → Actions → **Instagram sync** → Run workflow.
   Within ~2 minutes `instagram.json` should list the latest 12 posts, and the
   website band goes live on next page load (feed is fetched fresh, no
   redeploy of the website needed).

## Data contract

```jsonc
{
  "updated": "2026-07-20T02:00:00.000Z",
  "profile": { "username": "ctb.realty", "name": "CTB Realty", "url": "…" },
  "posts": [
    {
      "id": "1789…",
      "type": "IMAGE | VIDEO | CAROUSEL_ALBUM",
      "caption": "First 400 chars…",
      "permalink": "https://www.instagram.com/p/…/",
      "media": "instagram/media/<id>.jpg",   // self-hosted, preferred
      "mediaUrl": "https://…cdninstagram…",  // CDN fallback
      "timestamp": "2026-07-18T09:00:00+0000"
    }
  ]
}
```

The website renderer (`shared/js/instagram-feed.js` in the website repo) shows
up to 8 posts, newest first, linking each tile to the post on Instagram.
