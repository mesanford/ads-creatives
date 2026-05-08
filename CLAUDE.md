# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This repo has two concerns that work together:
1. **Python pipeline packages** (`meta-api/`, `gads_backfill/`, `bing-ads/`) — GCP Cloud Functions that pull ad data from three platforms and write to BigQuery (performance) and Firestore/Storage (creative gallery).
2. **Next.js frontend** (`creatives-gallery/`) — a read-only gallery that queries Firestore in real time and displays ad creatives.

**GCP Project:** `looker-studio-pro-msanford`  
**Firebase/Firestore collection:** `ad_creatives`  
**Firebase Storage paths:** `creatives/meta/`, `creatives/gads/`, `creatives/bing/`

---

## Commands

### Next.js gallery (`creatives-gallery/`)
```bash
npm run dev      # local dev server
npm run build    # production build
npm run lint     # ESLint
```

### Python pipelines (run from the pipeline subdirectory)
Install dependencies into a virtualenv before running:
```bash
pip install -r requirements.txt
```

Run a gallery pipeline locally (requires GCP ADC and `GCP_PROJECT` env var for Bing):
```bash
python meta-api/meta_creatives_gallery.py
python gads_backfill/gads_creatives_gallery.py
python bing-ads/bing_creatives_gallery.py   # also needs GCP_PROJECT env var
python bing-ads/main.py                     # full Bing pipeline (perf + gallery)
```

Deploy a Cloud Function manually (example for meta):
```bash
gcloud functions deploy meta-creatives-gallery \
  --runtime python311 \
  --entry-point run_meta_creatives_gallery_pipeline \
  --source meta-api \
  --project looker-studio-pro-msanford \
  --region us-central1 \
  --trigger-http \
  --security-level secure-always
```

Deploy Firestore rules:
```bash
firebase deploy --only firestore:rules
```

---

## Architecture

### Python pipelines — dual-purpose per package

Each of the three pipeline packages (`meta-api/`, `gads_backfill/`, `bing-ads/`) contains two independent pipelines bundled into one Cloud Function source directory:

| File | Purpose | BQ dataset |
|---|---|---|
| `main.py` | **Performance pipeline** — pulls ad metrics into BigQuery | `nueske_retail_meta_v2`, `nueske_retail_gads_v2`, `nueske_msads_data` |
| `*_creatives_gallery.py` | **Gallery pipeline** — downloads creative assets → Firebase Storage, writes metadata to Firestore `ad_creatives` | Firestore only |

`main.py` in each package imports the gallery module's entry point and re-exports it so both functions can be deployed from the same source directory. The CI/CD workflow deploys only the gallery entry point per function (see `.github/workflows/deploy-pipelines.yml`).

**Gallery pipeline entry points** (what CI deploys):
- `meta-api` → `run_meta_creatives_gallery_pipeline`
- `gads_backfill` → `run_creative_gallery_pipeline`
- `bing-ads` → `run_bing_creatives_gallery_pipeline`

### Firestore document schema

All three platforms write to the `ad_creatives` collection. Documents are keyed as `{platform}_{ad_id}` (e.g., `meta_123456`, `gads_789`, `bing_456`). Common fields across all platforms:

```
ad_id, platform, ad_name, headline, ad_text,
source_asset_url, firebase_storage_url, final_url, updated_at
```

Google Ads documents additionally include `account_id`, `campaign_name`, `group_type`, `creative_type`. Bing documents include `campaign_name`, `ad_group_name`.

### Credentials — all via GCP Secret Manager

No credentials are stored in code or env files. All secrets are fetched at runtime with `get_secret()` using the Cloud Function's service account. Required secrets per platform:

- **Meta:** `FB_APP_ID`, `FB_APP_SECRET`, `FB_ACCESS_TOKEN`, `FB_AD_ACCOUNT_ID`
- **Google Ads:** `GOOGLE_ADS_CREDENTIALS` (JSON blob with OAuth2 fields)
- **Bing:** `MS_CLIENT_ID`, `MS_DEV_TOKEN`, `MS_CUSTOMER_ID`, `MS_ACCOUNT_ID`, `MS_CLIENT_SECRET`, `MS_REFRESH_TOKEN`

The Bing pipeline automatically rotates `MS_REFRESH_TOKEN` in Secret Manager when OAuth returns a new one.

### Idempotency strategy

Each platform uses a different deduplication approach:
- **Meta:** MERGE into BigQuery on `(date, ad_id)`. Gallery uses `batch.set()` (upsert by doc ID).
- **Google Ads:** DELETE then INSERT for each target date. Gallery uses `batch.set()`.
- **Bing:** DELETE existing dates then WRITE_APPEND. Gallery uses `batch.set()`.

### Next.js gallery

`creatives-gallery/` is a single-page Next.js 16 app (uses React 19). The entire app is one client component (`src/app/page.tsx`) that opens a Firestore `onSnapshot` listener on `ad_creatives` ordered by `updated_at` desc. Platform filtering happens client-side. `src/components/CreativeCard.tsx` renders each ad. Firebase is initialized in `src/lib/firebase.ts` with hardcoded (non-secret) web SDK config.

> **Note:** This project uses Next.js 16 (see `creatives-gallery/AGENTS.md`). APIs and file conventions may differ from earlier versions — read `node_modules/next/dist/docs/` before writing Next.js code.

### CI/CD

`.github/workflows/deploy-pipelines.yml` deploys independently: each job checks `contains(github.event.head_commit.modified, '<dir>/')` and only fires when files in that directory changed. Requires `GCP_SA_KEY` secret in GitHub.

### Firestore security rules

Current rules allow open read/write until **2026-06-07**. Rules must be updated before expiry or all client requests will be denied.
