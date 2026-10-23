# Step 5: Embeddings & Semantic Search — 🧠 Second Green Light!

```
  🏁 LAUNCH ──→ 🔐 SIGN IN ──→ ✅ KEYWORD ──→ ▶ 🧠 SEMANTIC ──→ 🤖 RAG ──→ 🚀 YOURS
                                                   ~~~~~~~~~~~~
                                                   YOU ARE HERE
```

## What You'll Learn
- What vector embeddings are and how they represent meaning
- Why we split text into sentences (chunking strategy)
- How OpenAI's embedding API works
- How a vector index (HNSW) makes similarity search fast
- What Edge Functions are and why we need them
- How to deploy serverless functions with the Supabase CLI

## Why This Matters

Keyword search is powerful, but it only finds exact matches. Try searching for *"How can I find peace during hard times?"* with keyword search — you probably won't get great results. Semantic search solves this by understanding **meaning**, not just words. After this step, that same question will find talks about comfort, hope, and overcoming trials.

> 💪 **This is the biggest step in the assignment** — embedding generation, database updates, CLI setup, and edge function creation. You've got this!

## What to Do

### Step 5a: Generate Embeddings

Check what it will cost first (this reads no API key and makes no API calls):

```bash
python scripts/04_embed_data.py --dry-run
```

Then generate the embeddings:

```bash
python scripts/04_embed_data.py
```

This script:
1. Reads `scripts/output/sentences.json`
2. Calls OpenAI's `text-embedding-3-small` to generate a 1,536-dimensional vector for each sentence
3. Appends each batch to `scripts/output/embeddings.jsonl` (one line per sentence) and flushes it to disk
4. Resumes if interrupted: re-running only embeds sentences that don't have a line yet (or whose text changed)

> ⏱️ For ~26,000 sentences this makes about 100 API requests and costs about **$0.01–0.02**.
> 💰 **Your embeddings are safe**: They're saved to disk in `scripts/output/`, so even if the next step fails, you won't pay twice.

### Step 5b: Update Database with Embeddings

```bash
python scripts/05_update_embeddings.py
```

This reads the saved embeddings, updates each existing row (matched by `talk_id` + `sentence_num`) through the `set_embeddings()` database function, checks that every row got an embedding, and then builds an **HNSW index** on the embedding column.

> 💡 **Why build the index after loading?** Building an HNSW graph in one pass over all the vectors is faster and produces a better graph than inserting rows into an existing index one at a time.

> 💡 **Why `halfvec`?** Embeddings are stored as 16-bit floats (`halfvec(1536)`), which halves the size of the table and the index. That precision is plenty for ranking by similarity, and it keeps the project well under the free tier's 500 MB database limit.

### How Embeddings Work

```
"Faith is the assurance of things hoped for"
    ↓ OpenAI text-embedding-3-small
[0.012, -0.034, 0.089, ... 1,536 dimensions]
```

Similar sentences produce similar vectors. This is what enables semantic search — finding content by **meaning** rather than exact keywords.

> 💡 **Ask your AI assistant**: *"What are the trade-offs between different chunking strategies in RAG systems? Why did we choose sentence-level chunking?"*

### Step 5c: Create & Deploy the embed-question Edge Function

Your app needs to call OpenAI's API to embed user questions, but you can't put your API key in client-side JavaScript (anyone could steal it). Edge Functions solve this:

```
❌ Bad:  Browser → OpenAI API (API key exposed in browser!)
✅ Good: Browser → Edge Function → OpenAI API (API key stays on server)
```

**You'll create an Edge Function called `embed-question`** that:
- Receives a question from the browser
- Calls OpenAI to generate an embedding vector
- Returns the embedding to the browser

#### 1. Install the Supabase CLI

> 💡 **Ask your AI assistant**: *"How do I install the Supabase CLI on my operating system?"*

Common installation methods:
- **npm**: `npm install -g supabase`
- **Homebrew (macOS)**: `brew install supabase/tap/supabase`
- **Scoop (Windows)**: `scoop bucket add supabase https://github.com/supabase/scoop-bucket.git` then `scoop install supabase`

Verify: `supabase --version`

#### 2. Initialize Supabase

```bash
supabase init
```

This creates `supabase/config.toml`. You don't need `supabase link` (it asks for your database password); instead, pass `--project-ref YOUR_PROJECT_REF` (from `config.secret.json`) to each command below. The CLI authenticates with your access token: run `supabase login`, or set the `SUPABASE_ACCESS_TOKEN` environment variable.

#### 3. Set Your OpenAI Key as a Secret

Don't type the key on the command line (it ends up in your shell history and on screen). Put it in a file that is never committed and load it from there:

```bash
supabase secrets set --env-file path/to/secrets.env --project-ref YOUR_PROJECT_REF
```

where `secrets.env` contains one line, `OPENAI_API_KEY=...`. Delete the file afterwards. `supabase secrets list --project-ref YOUR_PROJECT_REF` shows each secret's name and a digest, never its value.

> 💡 Give the app its own OpenAI **project** key with a monthly budget limit, so a bug or abuse can't run up a large bill.

#### 4. Create the embed-question Function

Ask your AI assistant to help you create the Edge Function:

> 🤖 **Tell your AI assistant**: *"I need to create a Supabase Edge Function called `embed-question`. It should:*
> - *Accept a POST request with `{ "question": "..." }` in the body (and `{ "ping": true }` as a free readiness check)*
> - *Use the OpenAI API to generate an embedding with `text-embedding-3-small`*
> - *Return `{ "embedding": [...] }` in the response*
> - *Handle CORS for my GitHub Pages origin only (not `*`)*
> - *Verify the caller is a signed-in user, limit the question length, and enforce a per-user rate limit before calling OpenAI"*

The function lives at: `supabase/functions/embed-question/index.ts`, with shared helpers in `supabase/functions/_shared/cors.ts` and `supabase/functions/_shared/auth.ts`.

> ⚠️ **Auth note**: Keep `verify_jwt = true` (the default) for this function in `supabase/config.toml`, so the platform rejects requests without a valid JWT before your code runs. Inside the function, confirm the JWT belongs to a **user** (not just an API key) with `createSupabaseContext(req, { auth: 'user' })` from the official `@supabase/server` package. Don't use the legacy `SUPABASE_ANON_KEY`; Supabase is retiring the anon/service_role keys.

> 🔒 **Rate limiting**: the function calls the `consume_rate_limit()` database function *with the user's own token*. The database reads the user's id from the verified JWT (`auth.uid()`), so the function never needs the secret key, and users can't reset their own counters because the table itself isn't writable by them.

#### 5. Deploy the Function

```bash
supabase functions deploy embed-question --project-ref YOUR_PROJECT_REF --use-api
```

`--use-api` bundles the function on Supabase's servers, so you don't need Docker or Deno installed.

### 3. See Your Second Green Light! 🟢

After deploying the embed-question function:

1. **Refresh your site** (hard refresh: Ctrl+Shift+R)
2. The **🧠 Semantic Search** panel should now show **🟢 Ready**!

Try searching for *"How can I find peace during hard times?"* — notice how it finds relevant talks even without those exact keywords!

## Verification

- [ ] `04_embed_data.py --dry-run` shows the estimated cost before you spend anything
- [ ] `04_embed_data.py` completes and reports the actual tokens and cost
- [ ] `scripts/output/embeddings.jsonl` exists (your safety net!)
- [ ] `05_update_embeddings.py` reports every row updated and the HNSW index built
- [ ] `supabase --version` shows a version number
- [ ] `embed-question` function deployed without errors
- [ ] You can see the function in Supabase Dashboard → Edge Functions
- [ ] On your site: **🧠 Semantic Search** turns green and returns results!

## 🎉 Milestone: Second green light! Semantic Search works!

## → Next: [Step 06: RAG — All Lights Green](06_rag.md)
