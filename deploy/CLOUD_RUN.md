# Deploying on Google Cloud Run + GitHub Pages

The API runs on Cloud Run (scales to zero, a few seconds to wake, within the free tier at low
traffic); the static page is on GitHub Pages (always instant). Prices are baked into the image at
build time and refreshed in the background once they are a day old.

## Once

1. A Google Cloud project with billing linked, and a budget alert (Billing → Budgets & alerts).
2. The gcloud CLI, logged in with that project selected: `brew install --cask google-cloud-sdk`, then `gcloud init`.
3. Enable the services:

   ```bash
   gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
   ```

4. Fill in the settings (this file is git-ignored):

   ```bash
   cp deploy/cloudrun.env.example.yaml deploy/cloudrun.env.yaml
   ```

## Deploy (and redeploy after changes)

```bash
gcloud run deploy boodschappen-agent --source . --region europe-west4 --allow-unauthenticated --env-vars-file deploy/cloudrun.env.yaml --max-instances 3 --memory 512Mi --timeout 300 --cpu-boost
```

Cloud Build builds the Dockerfile in the cloud (no Docker needed locally). The command prints the
service URL, e.g. `https://boodschappen-agent-abc123-ez.a.run.app`; check `<URL>/api/health`.

## The page on GitHub Pages

1. Repository → Settings → Pages → Source: **GitHub Actions**.
2. Settings → Secrets and variables → Actions → **Variables** → New variable
   `API_BASE_URL` = the Cloud Run URL.
3. Actions → **Deploy web page** → Run workflow. The page appears at
   `https://<github-user>.github.io/boodschappen-agent/`, the owner page at `…/admin.html`.

`CORS_ORIGINS` in `cloudrun.env.yaml` must contain the Pages origin
(`https://<github-user>.github.io`, without the path).
