# Step 2: Supabase & Login — 🔐 "I Can Log In!"

```
  🏁 LAUNCH ──→ ▶ 🔐 SIGN IN ──→ 🔍 KEYWORD ──→ 🧠 SEMANTIC ──→ 🤖 RAG ──→ 🚀 YOURS
                   ~~~~~~~~~~
                   YOU ARE HERE
```

## What You'll Learn
- What Supabase is and why we use it (Postgres + Auth + Edge Functions as a service)
- How API keys and access tokens work
- The difference between the **publishable** key (public) and the **secret** key (admin)
- How magic link authentication works

## Why This Matters

Your app needs a database, authentication, and serverless functions. Instead of setting up three separate services, Supabase bundles them all together. Once you configure it, you'll be able to **log in to your own site** — a satisfying proof that your backend is connected.

## What to Do

### 1. Create a Supabase Project

1. Go to [https://supabase.com](https://supabase.com) and sign up / sign in
2. Click **"New Project"**
3. Fill in:
   - **Name**: `conference-rag` (or anything you like)
   - **Database Password**: Choose a strong password (save it!)
   - **Region**: Choose the one closest to you
4. Click **"Create new project"** — takes ~2 minutes to provision

### 2. Collect Your Credentials

You need values for **two config files**. Here's where to find each one:

Open **Project Settings → API Keys** in the Supabase Dashboard for the two keys below.

> ⚠️ Supabase is retiring the old `anon` / `service_role` JWT keys (the long `eyJ...` strings, under the "Legacy API Keys" tab). **Don't copy those.** Use the new `sb_publishable_...` and `sb_secret_...` keys instead.

#### config.public.json (safe to commit)

| Credential | Where to Find It |
|-----------|-----------------|
| `SUPABASE_URL` | Project Settings → API (or Data API) → Project URL |
| `SUPABASE_ANON_KEY` | Project Settings → API Keys → **Publishable key** (`sb_publishable_...`) |

> 📝 The field is still called `SUPABASE_ANON_KEY` so the code keeps working, but the value is your publishable key.

#### config.secret.json (never commit!)

| Credential | Where to Find It |
|-----------|-----------------|
| `SUPABASE_SERVICE_KEY` | Project Settings → API Keys → **Secret key** (`sb_secret_...`; click "Reveal") |
| `SUPABASE_PROJECT_REF` | Extract from your URL: `https://XXXXX.supabase.co` → `XXXXX` |
| `SUPABASE_ACCESS_TOKEN` | [Account Tokens page](https://supabase.com/dashboard/account/tokens) → Generate new token (`sbp_...`) |
| `OPENAI_API_KEY` | [OpenAI API Keys](https://platform.openai.com/api-keys) → Create new secret key |

> 🔐 **Treat the access token like a password to your whole Supabase account.** It can manage *every* project you own, not just this one. Only `01_create_schema.py` and the index build in `05_update_embeddings.py` use it, so give it a **short expiry** (a few days) and delete it when you're done.

### 3. Fill In Your Config Files

**config.public.json:**
```json
{
    "SUPABASE_URL": "https://xyzabc123.supabase.co",
    "SUPABASE_ANON_KEY": "sb_publishable_..."
}
```

**config.secret.json:**
```json
{
    "OPENAI_API_KEY": "sk-proj-...",
    "SUPABASE_ACCESS_TOKEN": "sbp_...",
    "SUPABASE_PROJECT_REF": "xyzabc123",
    "SUPABASE_SERVICE_KEY": "sb_secret_..."
}
```

> 💡 **Ask your AI assistant**: *"What is the difference between the Supabase publishable key and the secret key? Why is one safe to put in client-side code?"*

### 4. Push & Configure Auth

Commit and push your public config:

```bash
git add config.public.json
git commit -m "Add Supabase public credentials"
git push
```

Then configure the magic link redirect:

1. Go to **Supabase Dashboard** → **Authentication** → **URL Configuration**
2. Under "Redirect URLs", add **both** of these (the `/**` wildcard matches any page on that site):
   - `https://YOUR-USERNAME.github.io/conference-rag/**` (deployed site)
   - `http://localhost:5500/**` (for local development)

> ⚠️ **If you skip this step**, clicking the magic link will redirect to the wrong URL and login will fail.

### 5. Test Login

1. Wait ~1 minute for GitHub Pages to redeploy
2. Visit your deployed site
3. Enter your email and click "Sign In with Magic Link"
4. Check your inbox and click the link
5. You should be logged in! ✅

**Expected behavior**: You can log in, but all three search modes show **"Not Ready"** with red lights — that's correct! They'll light up as you complete the remaining steps.

> ⚠️ **Rate limit warning**: Supabase's built-in email service only allows a couple of magic link emails per hour. If you stop receiving emails, just wait and try again. Check spam too!

### 6. Turn Off New Sign-Ups 🔒

Now that you've logged in once, close the door behind you:

1. Go to **Authentication → Sign In / Providers** (the "User Signups" settings)
2. Turn **off** "Allow new users to sign up"
3. Save

Why? Every Edge Function you deploy later spends *your* OpenAI money for anyone who is "authenticated." With sign-ups open, anyone could create throwaway accounts with fake emails and burn your budget. With sign-ups off, "authenticated" means **only you**. (Your existing account keeps working. Turn sign-ups back on only if you deliberately want to invite others.)

> 💡 **Ask your AI assistant**: *"How do magic link logins work? What are the security advantages over passwords?"*

## Verification

- [ ] Supabase project is created and accessible at your dashboard
- [ ] `config.public.json` has your URL and publishable key (`sb_publishable_...`)
- [ ] `config.secret.json` has all four secret values filled in (secret key `sb_secret_...`, access token with a short expiry)
- [ ] Magic link login works on your deployed site
- [ ] New user sign-ups are turned off
- [ ] You see three search panels with red "Not Ready" indicators

## 🎉 Milestone: You can log in to YOUR site!

## → Next: [Step 03: Database Schema](03_database_schema.md)
