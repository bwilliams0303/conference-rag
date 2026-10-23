# Conference RAG

A Retrieval Augmented Generation (RAG) application that lets users ask questions about General Conference talks using semantic search and AI-generated answers.

- **Fork:** https://github.com/bwilliams0303/conference-rag
- **Live site:** https://bwilliams0303.github.io/conference-rag/ (sign-in required; new sign-ups are disabled on this project)

<p align="center">
  <img src="example.png" alt="Conference RAG screenshot" width="600">
  <br>
  <em>Sample screenshot — semantic search with AI-generated answers</em>
</p>

## 🚀 Quick Start

Follow the step-by-step guides in the `steps/` folder:

```
🏁 LAUNCH ──→ 🔐 SIGN IN ──→ 🔍 KEYWORD ──→ 🧠 SEMANTIC ──→ 🤖 RAG ──→ 🚀 YOURS
```

| Step | Milestone | Topic | Time |
|------|-----------|-------|------|
| [00](steps/00_overview.md) | — | Overview & Environment Setup | 15 min |
| [01](steps/01_fork_and_deploy.md) | 🏁 Launch | Fork & Deploy to GitHub Pages | 15 min |
| [02](steps/02_supabase_and_login.md) | 🔐 Sign In | Supabase Project & Login | 15 min |
| [03](steps/03_database_schema.md) | 🔍 Keyword (1/2) | Database Schema & RLS | 10 min |
| [04](steps/04_scrape_and_import.md) | 🔍 Keyword (2/2) | Scrape & Import → First green light! | 20 min |
| [05](steps/05_embeddings.md) | 🧠 Semantic | Embeddings & Edge Function | 25 min |
| [06](steps/06_rag.md) | 🤖 RAG | RAG Edge Function → All green! | 15 min |
| [07](steps/07_make_it_yours.md) | 🚀 Yours | Add Feature & Reflection | 20 min |

**Prerequisites:**
- [Supabase](https://supabase.com) account (free tier)
- [GitHub](https://github.com) account
- [OpenAI API key](https://platform.openai.com/api-keys) (about $0.02 of embeddings, plus RAG answers)
- Python 3.10+ (tested on 3.14)
- An AI coding assistant (Antigravity, GitHub Copilot, or Cursor)

## 🤖 AI-Assisted Development

This assignment is designed to be completed with an AI coding assistant. See [Step 00](steps/00_overview.md) for setup instructions.

Your AI assistant will automatically read [`ai_agent_instructions.md`](ai_agent_instructions.md) to understand the assignment's learning objectives and teach you along the way.

## 🏗️ Architecture

```
┌─────────────┐
│   Browser   │  Student asks question
│  (GitHub    │
│   Pages)    │
└──────┬──────┘
       │
       ├─── Supabase Auth (magic link)
       │
       ├─── Edge Function: embed-question
       │         ↓ OpenAI API (server-side 🔒)
       │         ↓ Returns embedding vector
       │
       ├─── Supabase Database (pgvector)
       │         ↓ match_sentences()
       │         ↓ Returns similar sentences
       │
       └─── Edge Function: generate-answer
                ↓ GPT-4o (server-side 🔒)
                ↓ Returns final answer
```

## ✨ My Feature: Question History

Every RAG answer is saved to a `question_history` table and shown in a **🕘 My History** panel (your last 20 questions, with the answer and how many talks it used).

- **Row Level Security:** policies let each user insert and read only rows where `user_id = (select auth.uid())`. `user_id` is filled in by the database (`DEFAULT auth.uid()`) and isn't in the column-level `INSERT` grant, so a user can't write rows as someone else even through the raw API.
- **Abuse limits:** CHECK constraints cap question/answer length and source count, and a trigger caps each user at 100 rows per day.
- **Proof:** [`scripts/sql/test_question_history_rls.sql`](scripts/sql/test_question_history_rls.sql) impersonates two users inside a transaction, shows neither can see the other's rows or insert as the other, and rolls everything back.

## 🔐 Security & Scale Changes in This Fork

- **Fixed a token leak:** the starter app saved `window.location.href` (which holds magic-link access/refresh tokens) into a table anyone could read. Visits are now counted by `record_page_view()` into one row per day; nothing about the visitor is stored and anonymous users can't grow the database.
- **Privileges + RLS on everything:** every table starts with `REVOKE ALL FROM anon, authenticated` (Supabase grants everything by default, including `TRUNCATE`, which RLS ignores), then grants back only what's needed. Functions revoke `EXECUTE` from `PUBLIC`; the two `SECURITY DEFINER` functions pin `search_path` and take identity only from `auth.uid()`.
- **New API keys:** publishable/secret keys instead of the retiring anon/service_role JWTs.
- **Edge Functions:** `verify_jwt = true` plus a user-session check (`@supabase/server`), CORS limited to this site, input size limits, per-user rate limits (`consume_rate_limit()`, called with the user's own token), and a free `{"ping": true}` readiness check. `generate-answer` receives only talk ids and fetches the talk text itself, so the browser can't inject prompt context.
- **Scale:** `halfvec(1536)` embeddings with an HNSW index (search went from ~550 ms to ~9 ms on 26k sentences), a `UNIQUE (talk_id, sentence_num)` key with idempotent, re-runnable imports, deterministic talk ids, and resumable embedding.
- **Supabase advisor:** the remaining warnings are intentional (`page_view_count`/`record_page_view` and `consume_rate_limit` are SECURITY DEFINER by design; `api_rate_limits` has RLS with no policies so users can't touch it).

## 🔍 Three Search Modes

| Mode | What it does | Requires |
|------|-------------|----------|
| **🔍 Keyword Search** | SQL `ILIKE` query on talk text | Conference data imported |
| **🧠 Semantic Search** | Vector similarity with pgvector | Embeddings + `embed-question` Edge Function |
| **🤖 Ask a Question (RAG)** | AI-generated answers with sources | All Edge Functions deployed |

## 📁 Project Structure

```
conference-rag/
├── index.html                  # Main application UI
├── app.js                      # Three search modes + auth logic
├── styles.css                  # Dark theme styling
├── config.js                   # Loads config.public.json for browser
├── config.public.json          # Supabase URL + publishable key (safe to commit)
├── config.secret.json          # API keys & secrets (git-ignored, YOU create this)
├── config.secret.example.json  # Template for config.secret.json
├── requirements.txt            # Python dependencies
├── .nojekyll                   # Tells GitHub Pages not to use Jekyll
├── steps/                      # Step-by-step assignment guides
│   ├── 00_overview.md
│   ├── ...
│   └── 07_make_it_yours.md
├── scripts/                    # Pipeline scripts (run in order)
│   ├── sql/test_question_history_rls.sql  # RLS proof for question history
│   ├── 01_create_schema.py     # Create DB schema
│   ├── 02_scrape_data.py       # Scrape conference talks → scripts/output/talks.json
│   ├── 03_import_data.py       # Import text to Supabase (🔍 keyword!)
│   ├── 04_embed_data.py        # Generate embeddings → scripts/output/embeddings.jsonl (💰 saved to disk, resumable)
│   ├── 05_update_embeddings.py # Update DB with embeddings + build HNSW index (🧠 semantic!)
│   └── output/                 # Intermediate data files (git-ignored)
└── supabase/                   # config.toml + Edge Functions (included; edit _shared/cors.ts for your origin)
    └── functions/
        ├── _shared/            # Shared auth & CORS helpers
        ├── embed-question/     # Converts questions to embeddings
        └── generate-answer/    # Generates AI answers
```

## 🔒 Security Model

| Component | Security Approach |
|-----------|------------------|
| Supabase publishable key | Safe to expose in `config.public.json` (protected by grants + RLS) |
| OpenAI API key | Server-side only via Edge Functions (in `config.secret.json`) |
| Database access | Row Level Security policies |
| Transport | HTTPS enforced by GitHub Pages |

## 📚 Learning Objectives

1. **Vector Embeddings** — Representing text as searchable numbers
2. **Semantic Search** — Finding similar content with cosine similarity
3. **RAG Architecture** — Combining retrieval + generation
4. **Edge Functions** — Serverless compute for secure API management
5. **Row Level Security** — User-level data access control
6. **Production Deployment** — Full-stack app on GitHub Pages

## 🎓 Assignment Deliverables

1. GitHub repository URL (your fork)
2. Live deployment URL (GitHub Pages)
3. Screenshot of a working query + answer
4. Your custom feature description
5. Written reflection on security, search, and AI-assisted development

## 🆘 Troubleshooting

| Issue | Solution |
|-------|----------|
| "Please configure Supabase" | Update `config.public.json` with your project URL and publishable key |
| Magic link not working | Add your site URL to Supabase → Authentication → URL Configuration |
| Magic link email not arriving | The built-in email sender allows only **a couple per hour** — wait and try again, check spam |
| Changes don't appear | Hard refresh (Ctrl+Shift+R) or try incognito window |
| Search shows "Not Ready" | Complete the corresponding step first |
| "Failed to get embedding" | Deploy Edge Functions and set OPENAI_API_KEY secret |
| "Database search failed" | Run `scripts/01_create_schema.py` |
| No search results | Import data with `scripts/03_import_data.py` |

## 📄 License

Educational use only. Conference talk content is used under fair use for educational purposes.

---

Built with ❤️ for CS 452
