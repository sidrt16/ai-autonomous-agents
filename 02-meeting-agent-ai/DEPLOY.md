# Deploy to Render — step by step

## Step 1 — Push to GitHub

```bash
cd "/Users/[YOUR_USERNAME]/Downloads/Meeting Agent"

# Clone your repo
git clone [https://github.com/](https://github.com/)[YOUR_GITHUB_USERNAME]/[YOUR_REPO_NAME].git
cd [YOUR_REPO_NAME]

# Copy the meeting proxy folder in
cp -r "/Users/[YOUR_USERNAME]/Downloads/Meeting Agent/meeting-proxy-agent" .

cd meeting-proxy-agent
git add .
git commit -m "add meeting proxy agent"
git push

Step 2 — Deploy on Render
Go to https://render.com → sign in with your account

New → Web Service

Connect GitHub → select your repository

Set Root Directory to meeting-proxy-agent

Runtime: Python 3

Build command: pip install -r requirements.txt

Start command: uvicorn app.main:app --host 0.0.0.0 --port $PORT

Click Create Web Service — Render gives you a URL like https://[YOUR_SERVICE_NAME].onrender.com

Step 3 — Add environment variables in Render
In your Render service → Environment → add each:

GOOGLE_CLIENT_ID      = YOUR_GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET  = YOUR_GOOGLE_CLIENT_SECRET
GOOGLE_REDIRECT_URI   = https://YOUR_RENDER_URL/auth/google/callback

MS_CLIENT_ID          = YOUR_MICROSOFT_CLIENT_ID
MS_CLIENT_SECRET      = YOUR_MICROSOFT_CLIENT_SECRET
MS_TENANT_ID          = common
MS_REDIRECT_URI       = https://YOUR_RENDER_URL/auth/outlook/callback

ZOOM_CLIENT_ID        = YOUR_ZOOM_CLIENT_ID
ZOOM_CLIENT_SECRET    = YOUR_ZOOM_CLIENT_SECRET
ZOOM_REDIRECT_URI     = https://YOUR_RENDER_URL/auth/zoom/callback

ANTHROPIC_API_KEY     = YOUR_ANTHROPIC_API_KEY
APP_SECRET_KEY        = YOUR_GENERATED_RANDOM_SECRET_KEY
APP_BASE_URL          = https://YOUR_RENDER_URL
ENV                   = production
CONFIRMATION_TOKEN_TTL_MINUTES = 15
Replace YOUR_RENDER_URL with the actual URL Render gave you, and replace the placeholder text with your freshly rotated keys.

Step 4 — Update redirect URIs
Google Cloud Console
Go to https://console.cloud.google.com → APIs & Services → Credentials

Edit your OAuth client → Authorized redirect URIs

Add: https://YOUR_RENDER_URL/auth/google/callback

Azure Portal
Go to https://portal.azure.com → App registrations → Meeting Proxy

Authentication → Add redirect URI

Add: https://YOUR_RENDER_URL/auth/outlook/callback

Zoom Marketplace
Go to https://marketplace.zoom.us → your Meeting Proxy app

Basic Information → OAuth Redirect URL

Set to: https://YOUR_RENDER_URL/auth/zoom/callback

OAuth Allow Lists → add: https://YOUR_RENDER_URL

Step 5 — Configure Zoom App surfaces
In Zoom Marketplace → your app → Features → Surface:

Check In-meeting

Home URL: https://YOUR_RENDER_URL/app

Scopes → add:

meeting:read

user:read

Save and click Add app (Local Test) to install it in your Zoom account.

Step 6 — Test it
Open Zoom → start or join a meeting

Click Apps in the toolbar → find Meeting Proxy → click it

The sidebar opens → connect Google Calendar → pick a meeting → start proxy

Step 7 — Get your Anthropic API key
If you don't have one yet:

Go to https://console.anthropic.com

API Keys → Create Key

Copy it and add it to Render environment variables as ANTHROPIC_API_KEY

One thing to know about Render free tier
Free tier services spin down after 15 minutes of inactivity and take ~30 seconds to wake up on the next request. For production use, upgrade to the $7/month Starter plan to keep it always-on. For testing the free tier is fine.tivity and take ~30 seconds to wake up on the next request. For production use, upgrade to the $7/month Starter plan to keep it always-on. For testing the free tier is fine.
