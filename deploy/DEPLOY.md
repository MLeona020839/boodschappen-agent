# Deploying the web app on a small server

One always-on server runs two containers: the FastAPI app (API + static web page) and Caddy
(HTTPS with automatic certificates, compression, streaming). Any Linux VPS with Docker works;
these steps use Hetzner Cloud (about €4–5 a month for the smallest plan).

## 1. Create the server (in your browser)

1. Sign up at Hetzner Cloud and create a project.
2. **Add server**: a location in the EU, image **Ubuntu 24.04**, the smallest shared-vCPU plan
   with at least 2 GB RAM.
3. **SSH key**: on your Mac run `cat ~/.ssh/id_ed25519.pub` (create one first with
   `ssh-keygen -t ed25519` if the file doesn't exist) and paste the output.
4. **Firewall**: allow inbound TCP 22, 80 and 443.
5. Create the server and note its IPv4 address, for example `49.12.34.56`.

## 2. Install and start (over SSH)

```bash
ssh root@49.12.34.56
```

```bash
apt update && apt install -y docker.io docker-compose-v2 git
```

```bash
git clone https://github.com/MLeona020839/boodschappen-agent.git && cd boodschappen-agent/deploy
```

```bash
cp .env.example .env && nano .env
```

Fill in `.env`:

- `SITE_ADDRESS`: your domain, or the server IP with dashes plus `.sslip.io`
  (`49-12-34-56.sslip.io`), which gives you HTTPS without buying a domain.
- `ANTHROPIC_API_KEY`, `DAILY_BUDGET_EUR`, `SESSION_BUDGET_EUR`, `ADMIN_CODE`.
- `PROMOS_DATABASE_URL`: the Supabase connection string, so promotions and the daily spend are
  shared with the Streamlit app and survive rebuilds.

```bash
docker compose up -d --build
```

The first start downloads the prices (about 20 seconds). Then open `https://<SITE_ADDRESS>`.
The owner page is `https://<SITE_ADDRESS>/admin.html`.

## 3. Day to day

| Task | Command (in `boodschappen-agent/deploy`) |
|---|---|
| Update to the latest code | `git pull && docker compose up -d --build` |
| See logs | `docker compose logs -f app` |
| Restart | `docker compose restart app` |
| Change settings | edit `.env`, then `docker compose up -d` |

Prices refresh automatically once a day. Keep a monthly spend limit in the Anthropic Console as a
last line of defence.
