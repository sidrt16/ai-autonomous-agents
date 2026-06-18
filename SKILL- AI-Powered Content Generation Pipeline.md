# SKILL: AI-Powered Content Generation Pipeline

**Version:** 1.0  
**Project:** Claude to Instagram  
**Stack:** n8n Cloud · Claude Haiku · gpt-image-2 · Cloudinary · Meta Graph API  
**Monthly cost:** ~$27.30 at 3 posts/day  

---

## What this skill covers

This file gives you everything needed to build, run, and debug a fully automated Instagram content pipeline. It was built from scratch and documents every error, fix, and lesson learned. Use it as a reference, a plug-in context file, or a setup checklist.

---

## Architecture overview

```
Schedule Trigger (every 8hrs)
  → HTTP Request: Claude Haiku API         [generates content as JSON]
  → Code: JSON Parse                        [extracts fields, fixes edge cases]
  → HTTP Request: gpt-image-2 API          [generates image as base64]
  → Code: Cloudinary Upload                 [converts base64 → public JPEG URL]
  → HTTP Request: Meta Graph API /media    [creates Instagram media container]
  → HTTP Request: Meta Graph API /publish  [publishes container to Instagram]
```

**Total nodes:** 7 (1 trigger + 4 HTTP + 2 Code)  
**Execution time:** ~12 seconds end to end  
**Posts per day:** 3 (every 8 hours)

---

## Required accounts and credentials

| Credential | Where to get it | Notes |
|---|---|---|
| Anthropic API key | console.anthropic.com → API Keys | Add $5 credits minimum |
| OpenAI API key | platform.openai.com → API Keys | Set permissions to "All" |
| Cloudinary Cloud Name | cloudinary.com → Dashboard | Free tier, 25GB storage |
| Meta System User token | business.facebook.com → Users → System Users | Set to "Never" expire |
| n8n Cloud account | n8n.io | Starter plan $24/mo |

---

## Node 1: Schedule Trigger

**Node type:** Schedule Trigger  
**Setting:** Every 8 hours  

```
Trigger interval: Hours
Hours between triggers: 8
```

**Critical:** The n8n workflow must be clicked **Publish** (top-right button) for the schedule to run automatically. An unpublished workflow only executes when you manually click "Execute workflow." This is the most commonly missed step.

---

## Node 2: Claude Haiku API

**Node type:** HTTP Request  
**Method:** POST  
**URL:** `https://api.anthropic.com/v1/messages`

### Required headers (all three are mandatory)

```
x-api-key: YOUR_ANTHROPIC_KEY
anthropic-version: 2023-06-01
content-type: application/json
```

Missing `content-type` causes a 400 Bad Request error.

### JSON body

```json
{
  "model": "claude-haiku-4-5-20251001",
  "max_tokens": 600,
  "messages": [
    {
      "role": "user",
      "content": "Instagram post for AI Analytics niche. Audience: marketers who hate manual reporting. CRITICAL: Return ONLY raw JSON. No backticks. No markdown. Start with { end with }. Every post MUST be different. Rotate between these formats: stat-based (shocking number or percentage), story-based (before/after transformation), tip-based (one actionable trick), myth-busting (common belief vs reality), question-based (provocative question that stops the scroll). Pick a RANDOM format each time. Never repeat the same hook style twice. Fields: hook (1 sentence max 10 words, matches chosen format), script (50 words max), caption (start with hook, then 1 specific insight with a stat, end with 'Comment PROMPT and I ll DM you the template.' Max 80 words.), hashtags (10 relevant tags as single string), visual_desc (40 words max, must match the post format: stat-based=big number graphic on dark background, story-based=before/after split screen, tip-based=clean numbered checklist on dark background, myth-busting=crossed out myth with truth revealed, question-based=bold question text on dark background. No people. No stock photos. Bold white typography.)"
    }
  ]
}
```

### Output fields
Claude returns a JSON object with: `hook`, `script`, `caption`, `hashtags`, `visual_desc`

### Cost
~$0.015 per 100 calls with Haiku. At 90 posts/month ≈ $1.50/month.

---

## Node 3: JSON Parse (Code node)

**Node type:** Code (JavaScript)

```javascript
let raw = $input.first().json.content[0].text;

// Strip markdown backticks if Claude adds them despite instructions
raw = raw.replace(/```json/g, '').replace(/```/g, '').trim();

const parsed = JSON.parse(raw);

// Fix hashtags if Claude returns an array instead of a string
if (Array.isArray(parsed.hashtags)) {
  parsed.hashtags = parsed.hashtags.join(' ');
}

return [{ json: parsed }];
```

### Why each fix exists
- **Backtick strip:** Claude occasionally wraps output in ```json fences. Without stripping, `JSON.parse` throws `SyntaxError: Unexpected token`.
- **Hashtag array fix:** Claude sometimes returns hashtags as `["#AI", "#Analytics"]` instead of `"#AI #Analytics"`. Meta's API only accepts a string.

---

## Node 4: gpt-image-2 Image Generation

**Node type:** HTTP Request  
**Method:** POST  
**URL:** `https://api.openai.com/v1/images/generations`

### Headers

```
Authorization: Bearer YOUR_OPENAI_KEY
Content-Type: application/json
```

### JSON body

```json
{
  "model": "gpt-image-2",
  "prompt": "{{ $('Code in JavaScript').item.json.visual_desc }}. Professional infographic. Dark navy background. Bold white typography. No people. Square 1:1 format.",
  "size": "1024x1024",
  "quality": "low",
  "output_format": "jpeg",
  "n": 1
}
```

### Critical gotchas

- `gpt-image-2` **always returns base64**, never a URL. The response is in `data[0].b64_json`.
- `quality: "low"` is 15× faster and cheaper than `"high"`. For Instagram posts, low quality is visually indistinguishable.
- `output_format` accepts `"jpeg"`, `"png"`, or `"webp"`. Use `"jpeg"` — Meta requires JPEG.
- `response_format` is **not** a valid parameter for this model. Do not use it.
- `standard` is **not** a valid quality value. Valid values: `"low"`, `"medium"`, `"high"`, `"auto"`.
- **OpenAI organization verification** is required before the first API call. Go to platform.openai.com → Organization Settings → verify.
- `gpt-image-2` replaced DALL-E 3 in April 2026. Use `gpt-image-2` not `dall-e-3`.

### Cost
~$0.02 per image at low quality. 90 images/month ≈ $1.80/month.

---

## Node 5: Cloudinary Upload (Code node)

**Node type:** Code (JavaScript)

```javascript
const base64Data = $input.first().json.data[0].b64_json;

const cloudName = "YOUR_CLOUDINARY_CLOUD_NAME";
const uploadPreset = "ml_default";

const response = await this.helpers.httpRequest({
  method: "POST",
  url: `https://api.cloudinary.com/v1_1/${cloudName}/image/upload`,
  headers: { "Content-Type": "application/json" },
  body: {
    file: `data:image/jpeg;base64,${base64Data}`,
    upload_preset: uploadPreset
  },
  json: true
});

return [{ json: { image_url: response.secure_url } }];
```

### Cloudinary setup
1. Sign up at cloudinary.com (free)
2. Go to Settings → Upload → Upload presets
3. Ensure `ml_default` exists and is set to **Unsigned**
4. Copy your Cloud Name from the Dashboard

### Why not Google Drive?
Meta's Graph API requires a direct link to the raw image file with no redirects. Google Drive links redirect through `drive.google.com` before serving the file. Meta rejects these silently — the container creation returns success but the post never appears. Cloudinary serves direct HTTPS URLs with no redirects.

### n8n HTTP helper note
In n8n Code nodes, `fetch` and `$http` are not available. Use `this.helpers.httpRequest()` instead.

---

## Node 6: Meta Graph API — Create Media Container

**Node type:** HTTP Request  
**Method:** POST  
**URL:** `https://graph.facebook.com/v19.0/{ig-user-id}/media`

Replace `{ig-user-id}` with your Instagram Business Account ID (a number like `17841477633745405`).

### Headers

```
Content-Type: application/json
```

### JSON body (using n8n expressions)

Configure as "Using Fields Below" to avoid JSON parsing issues with n8n expressions:

```
Field: image_url
Value: {{ $json.image_url }}

Field: caption
Value: {{ $('Code in JavaScript').item.json.caption }}

{{ $('Code in JavaScript').item.json.hashtags }}

Field: access_token
Value: YOUR_SYSTEM_USER_TOKEN
```

Or as raw JSON if expressions are stable:

```json
{
  "image_url": "{{ $json.image_url }}",
  "caption": "{{ $('Code in JavaScript').item.json.caption }}\n\n{{ $('Code in JavaScript').item.json.hashtags }}",
  "access_token": "YOUR_SYSTEM_USER_TOKEN"
}
```

### Output
Returns `{ "id": "CONTAINER_ID" }`. Save this — it's needed for the publish step.

---

## Node 7: Meta Graph API — Publish Container

**Node type:** HTTP Request  
**Method:** POST  
**URL:** `https://graph.facebook.com/v19.0/{ig-user-id}/media_publish`

### Headers

```
Content-Type: application/json
```

### JSON body

```json
{
  "creation_id": "{{ $json.id }}",
  "access_token": "YOUR_SYSTEM_USER_TOKEN"
}
```

`{{ $json.id }}` pulls the container ID from the previous node output.

### Output
Returns `{ "id": "PUBLISHED_MEDIA_ID" }`. A different ID than the container ID confirms the post is live.

---

## Meta API setup checklist

Complete all of these before the pipeline will work:

- [ ] Instagram account switched to **Business** or **Creator** (not Personal)
- [ ] Instagram account connected to a **Facebook Page**
- [ ] Meta Developer app created at developers.facebook.com
- [ ] App mode set to **Live** (not Development) — requires a Privacy Policy URL
- [ ] **Instagram** product added to the app (Dashboard → Add Product → Instagram → Set up)
- [ ] System User created in Business Manager (business.facebook.com → Users → System Users)
- [ ] System User assigned to your app with **Full access**
- [ ] System User assigned to your Facebook Page with **Full access**
- [ ] Token generated for System User with these permissions:
  - `instagram_basic`
  - `instagram_content_publish`
  - `pages_show_list`
  - `pages_read_engagement`
  - `pages_manage_posts`
- [ ] Token expiry set to **Never**
- [ ] Instagram Business Account ID retrieved via:
  ```
  GET https://graph.facebook.com/v19.0/me/accounts?fields=id,name,instagram_business_account&access_token=TOKEN
  ```

---

## How to get your Instagram Business Account ID

After generating your System User token, call this URL in a browser:

```
https://graph.facebook.com/v19.0/me/accounts?fields=id,name,instagram_business_account&access_token=YOUR_TOKEN
```

Look for:
```json
{
  "instagram_business_account": {
    "id": "17841477633745405"
  }
}
```

That `id` is your Instagram User ID. Use it in both Meta API node URLs.

---

## Verifying a post published correctly

If a post seems to succeed but doesn't appear on Instagram, debug in this order:

### 1. Check container status
```
GET https://graph.facebook.com/v19.0/{container-id}?fields=status_code,status&access_token=TOKEN
```
Expected: `"status_code": "FINISHED"`

### 2. Check published media
```
GET https://graph.facebook.com/v19.0/{ig-user-id}/media?fields=id,caption,timestamp,media_url,permalink&access_token=TOKEN
```
If `data` is empty, the post did not publish.

### 3. Most common silent failures
- Image was PNG not JPEG — Meta accepts PNG at container creation but never publishes
- Google Drive image URL — redirects cause silent failure
- Token expired or missing `instagram_content_publish` permission
- Publish node body had wrong field name (`creation_id` not `container_id`)
- Publish node URL was `/media` not `/media_publish`

---

## Cost breakdown

| Service | Usage | Monthly cost |
|---|---|---|
| n8n Cloud Starter | Automation platform | $24.00 |
| Claude Haiku | 90 API calls (3/day) | ~$1.50 |
| gpt-image-2 low quality | 90 images (3/day) | ~$1.80 |
| Cloudinary | Image hosting | Free |
| Meta Graph API | Publishing | Free |
| **Total** | **90 posts/month** | **~$27.30** |

### Scaling costs

| Posts per day | Images/month | Est. monthly total |
|---|---|---|
| 1 | 30 | ~$25.50 |
| 3 (current) | 90 | ~$27.30 |
| 10 | 300 | ~$35.00 |

The n8n cost is fixed. Only Claude and image generation scale with volume.

---

## Common errors and fixes

| Error | Cause | Fix |
|---|---|---|
| `Bad Request` on Claude node | Missing `content-type` header | Add `content-type: application/json` header |
| `SyntaxError: Unexpected token` | Claude returned markdown-wrapped JSON | Add `.replace(/```json/g, '').replace(/```/g, '')` in Code node |
| `The model 'dall-e-3' does not exist` | DALL-E 3 retired April 2026 | Use `gpt-image-2` |
| `Invalid value: 'standard'` on image node | Wrong quality parameter | Use `"low"`, `"medium"`, or `"high"` |
| `Unknown parameter: 'response_format'` | Not supported by gpt-image-2 | Use `output_format` instead |
| `Cannot parse access token` | Token was truncated when copied | Regenerate token in Business Manager |
| `Insufficient developer role` | App in Development mode | Switch app to Live mode |
| `media_publish not found` | Wrong URL in publish node | Use `/media_publish` not `/media` |
| `data: []` from media endpoint | Post silently failed | Check image is JPEG, URL is direct (not Drive), token has publish permission |
| Schedule not running | Workflow not published | Click Publish button in n8n top-right |
| `fetch is not defined` | n8n Code node doesn't support fetch | Use `this.helpers.httpRequest()` |
| `$http is not defined` | n8n Code node doesn't support $http | Use `this.helpers.httpRequest()` |
| Rate limit from Meta | Too many test runs | Wait 5 minutes before retrying |
| Gemini image API errors | Free tier API calls blocked | Gemini image generation requires billing — use gpt-image-2 instead |

---

## Content strategy (what Claude generates)

The prompt rotates between five post formats to keep the feed varied:

| Format | Hook style | Visual style |
|---|---|---|
| Stat-based | Shocking number or percentage | Big number on dark background |
| Story-based | Before/after transformation | Split-screen comparison |
| Tip-based | One actionable trick | Numbered checklist |
| Myth-busting | Common belief vs reality | Crossed-out myth with truth |
| Question-based | Provocative question | Bold question text |

Each post outputs: `hook`, `script`, `caption`, `hashtags`, `visual_desc`

**Target audience:** Marketers, data analysts, growth teams who hate manual reporting  
**Voice:** Direct, zero jargon, "smart friend who saves you time"  
**CTA:** Every caption ends with "Comment PROMPT and I'll DM you the template."

---

## Monetization path

| Phase | Method | Timeline | Revenue estimate |
|---|---|---|---|
| 1 | Affiliate marketing (AI tools) | Month 1–2 | $200–500/mo |
| 2 | Digital products (prompt libraries on Gumroad) | Month 2–3 | $500–2K/mo |
| 3 | Done-for-you pipeline setup | Month 4+ | $2K–10K/mo |

---

## Roadmap (not yet built)

| Feature | What it does | Priority |
|---|---|---|
| Auto-DM | Meta Webhook detects "PROMPT" comment → Claude generates DM → sends automatically | High |
| Carousel posts | Multiple images per post (3–5 slides) using multiple gpt-image-2 calls | Medium |
| Reels | Google Veo API generates 8s video from `visual_desc` | Month 2 |
| Multi-account | Same workflow, different Claude system prompts, multiple Instagram accounts | Month 3 |

---

## Key IDs for this project

```
Instagram handle:          @eve_rydayai
Facebook Page:             Everydayai
Business Manager ID:       1559287519098479
Instagram Business ID:     17841477633745405
Meta Developer App ID:     26600184902994734
System User:               n8n bot
n8n workspace:             everydayai2.app.n8n.cloud
```

---

## Quick start checklist for a new setup

- [ ] Create n8n Cloud account (n8n.io, free trial)
- [ ] Add $5 Anthropic credits (console.anthropic.com)
- [ ] Create Anthropic API key
- [ ] Add $5 OpenAI credits (platform.openai.com)
- [ ] Create OpenAI API key (permissions: All)
- [ ] Complete OpenAI org verification
- [ ] Create Cloudinary account (cloudinary.com, free)
- [ ] Set `ml_default` upload preset to Unsigned in Cloudinary
- [ ] Switch Instagram to Business/Creator account
- [ ] Connect Instagram to a Facebook Page
- [ ] Create Meta Developer app (developers.facebook.com)
- [ ] Set app to Live mode + add Privacy Policy URL
- [ ] Add Instagram product to app
- [ ] Create System User in Business Manager
- [ ] Assign app + Facebook Page to System User
- [ ] Generate System User token (Never expire) with all 5 permissions
- [ ] Retrieve Instagram Business Account ID via Graph API
- [ ] Build all 7 nodes in n8n following the configs above
- [ ] Test each node individually before running full flow
- [ ] Execute full workflow and verify post appears on Instagram
- [ ] Change trigger from Manual to Schedule (every 8 hours)
- [ ] Click Publish in n8n

---

## Notes on model versions (June 2026)

- **Claude:** `claude-haiku-4-5-20251001` is the current Haiku model string
- **Image generation:** `gpt-image-2` replaced DALL-E 3 (retired April 2026)
- **Meta Graph API version:** `v19.0` — verified working
- **n8n:** Tested on Cloud Starter tier

Model names and API versions change. Always verify current model strings from the provider's documentation before building.
