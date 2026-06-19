# Deploy to Render — step by step

## Step 1 — Push to GitHub

```bash
cd "/Users/siddharthpawar/Downloads/Meeting Agent"

# Clone your repo
git clone https://github.com/sidrt16/ai-autonomous-agents.git
cd ai-autonomous-agents

# Copy the meeting proxy folder in
cp -r "/Users/siddharthpawar/Downloads/Meeting Agent/meeting-proxy-agent" .

cd meeting-proxy-agent
git add .
git commit -m "add meeting proxy agent"
git push
```

## Step 2 — Deploy on Render

1. Go to https://render.com → sign in with sidrt16@gmail.com
2. **New** → **Web Service**
3. Connect GitHub → select `sidrt16/ai-autonomous-agents`
4. Set **Root Directory** to `meeting-proxy-agent`
5. Runtime: **Python 3**
6. Build command: `pip install -r requirements.txt`
7. Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
8. Click **Create Web Service** — Render gives you a URL like `https://meeting-proxy-agent.onrender.com`

## Step 3 — Add environment variables in Render

In your Render service → **Environment** → add each:

```
GOOGLE_CLIENT_ID      = 18841577659-qpnedu7j6rfdg26iraj1fv9ru8aikl71.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET  = 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
GOOGLE_REDIRECT_URI   = https://YOUR_RENDER_URL/auth/google/callback

MS_CLIENT_ID          = 52dffc14-2c4b-4169-9c9d-34cba6e00f5a
MS_CLIENT_SECRET      = FeN8Q~dgIokZuY3Fr-gcgNdqb1l-zHcjdOQTna8F
MS_TENANT_ID          = common
MS_REDIRECT_URI       = https://YOUR_RENDER_URL/auth/outlook/callback

ZOOM_CLIENT_ID        = kLJPR5SUR7eO_Qa6UUC4GA
ZOOM_CLIENT_SECRET    = tDxPqd9PYFk5b8O5iENCvcxUN9JdpX0b
ZOOM_REDIRECT_URI     = https://YOUR_RENDER_URL/auth/zoom/callback

ANTHROPIC_API_KEY     = [your key from console.anthropic.com]
APP_SECRET_KEY        = db2f856cadd58a2355d31b4cce94d0dcb1006d6aa68876ceb6440484f2cb0923
APP_BASE_URL          = https://YOUR_RENDER_URL
ENV                   = production
CONFIRMATION_TOKEN_TTL_MINUTES = 15
```

Replace `YOUR_RENDER_URL` with the actual URL Render gave you.

## Step 4 — Update redirect URIs

### Google Cloud Console
1. Go to https://console.cloud.google.com → APIs & Services → Credentials
2. Edit your OAuth client → Authorized redirect URIs
3. Add: `https://YOUR_RENDER_URL/auth/google/callback`

### Azure Portal
1. Go to https://portal.azure.com → App registrations → Meeting Proxy
2. Authentication → Add redirect URI
3. Add: `https://YOUR_RENDER_URL/auth/outlook/callback`

### Zoom Marketplace
1. Go to https://marketplace.zoom.us → your Meeting Proxy app
2. Basic Information → OAuth Redirect URL
3. Set to: `https://YOUR_RENDER_URL/auth/zoom/callback`
4. OAuth Allow Lists → add: `https://YOUR_RENDER_URL`

## Step 5 — Configure Zoom App surfaces

In Zoom Marketplace → your app → **Features** → **Surface**:
- Check **In-meeting**
- Home URL: `https://YOUR_RENDER_URL/app`

**Scopes** → add:
- `meeting:read`
- `user:read`

Save and click **Add app** (Local Test) to install it in your Zoom account.

## Step 6 — Test it

1. Open Zoom → start or join a meeting
2. Click **Apps** in the toolbar → find **Meeting Proxy** → click it
3. The sidebar opens → connect Google Calendar → pick a meeting → start proxy

## Step 7 — Get your Anthropic API key

If you don't have one yet:
1. Go to https://console.anthropic.com
2. API Keys → Create Key
3. Copy it and add it to Render environment variables as `ANTHROPIC_API_KEY`

---

## One thing to know about Render free tier

Free tier services spin down after 15 minutes of inactivity and take ~30 seconds to wake up on the next request. For production use, upgrade to the $7/month Starter plan to keep it always-on. For testing the free tier is fine.
