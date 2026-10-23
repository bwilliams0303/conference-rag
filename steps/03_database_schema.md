# Step 3: Database Schema — Setting Up for 🔍 Keyword Search

```
  🏁 LAUNCH ──→ 🔐 SIGN IN ──→ ▶ 🔍 KEYWORD ──→ 🧠 SEMANTIC ──→ 🤖 RAG ──→ 🚀 YOURS
                                   ~~~~~~~~~~~
                                   YOU ARE HERE (step 1 of 2)
```

## What You'll Learn
- What pgvector is and why it's useful for AI applications
- How vector similarity search works at the database level
- What Row Level Security (RLS) is and why it matters
- How database functions (stored procedures) work
- **The difference between public and protected data access**

## Why This Matters

Your database is the foundation for all three search modes. The schema you create here includes the `sentence_embeddings` table (where all your conference talk data will live) and the `match_sentences()` function (which powers semantic search later). Getting this right means everything else builds on a solid base.

## What to Do

### 1. Run the Schema Creation Script

```bash
python scripts/01_create_schema.py
```

This script creates:
- **pgvector extension** — Adds vector data type and similarity operators to PostgreSQL
- **`sentence_embeddings` table** — Stores talk sentences with their embedding vectors (🔒 **auth-only**)
- **`page_views` / `page_view_daily` tables** — The page visit counter (🌍 **public**, count-only; visitors can't read or write rows directly)
- **`question_history` table** — Each user's private log of past questions (the Step 07 feature, 🔒 **per-user RLS**)
- **`api_rate_limits` table** — Per-user counters for the Edge Functions (no direct access for users)
- **RLS policies + privileges** — Controls who can access each table
- **Database functions** — `match_sentences()` (vector search), `page_view_count()` and `record_page_view()` (the counter), `consume_rate_limit()` (rate limiting), plus `set_embeddings()` and `reset_sentence_embeddings()` (used by the import scripts)

> 🔢 The scripts are numbered **01–05** and run in order (the "Step 1…5" in their docstrings refers to these script numbers, not the numbered guides in `steps/`).

> 💡 **Ask your AI assistant**: *"What is pgvector and how does cosine similarity search work?"*

### Who Can Use What

| Object | Who can use it? | Why? |
|--------|----------------|------|
| `sentence_embeddings` (table) | 🔒 Read: signed-in users. Write: service role only | Protected content — requires login |
| `page_views` / `page_view_daily` (tables) | 🔢 Nobody directly; anyone gets the total via `page_view_count()` | Public analytics — visitors' rows stay private |
| `record_page_view()` | 🌍 Anyone | Counts a visit into `page_view_daily` (one row per day), so anonymous visitors can't fill your database |
| `question_history` (table) | 🔒 Signed-in users: read and insert **their own** rows only | Private per-user data (per-user RLS) |
| `api_rate_limits` (table) | ⛔ Nobody via the API | Only `consume_rate_limit()` touches it |
| `consume_rate_limit()` | 🔒 Signed-in users (called by the Edge Functions) | Limits how much OpenAI spend one user can cause |
| `match_sentences()` | 🔒 Signed-in users | Semantic search, still subject to RLS |
| `set_embeddings()`, `reset_sentence_embeddings()` | 🔑 Service role only | Used by scripts 03 and 05 |

This is **Row Level Security (RLS)** in action. These tables all have RLS enabled, but their *policies* and privileges define very different access rules.

### Understanding the Schema

```sql
-- sentence_embeddings: Protected data
CREATE POLICY "Allow authenticated users to read"
ON sentence_embeddings FOR SELECT
TO authenticated          -- ← Only logged-in users
USING (true);

-- question_history: Private per-user data
CREATE POLICY "Users read own history"
ON question_history FOR SELECT
TO authenticated
USING ((select auth.uid()) = user_id);   -- ← Only YOUR rows
```

Postgres checks two layers, in order:

1. **Privileges** (`GRANT` / `REVOKE`) — can this role touch this table or column *at all*? If not, the request fails with `permission denied`.
2. **RLS policies** — of the rows the role may touch, *which ones*? Rows that fail the policy are silently filtered out.

The script uses both. For example, anonymous visitors get no direct privileges on the page-view tables at all. The site calls `record_page_view()` to count a visit and `page_view_count()` to read the total; both are `SECURITY DEFINER` functions that run as the table owner and expose nothing but that one action. And `question_history` has a policy of `auth.uid() = user_id`, so each user sees only their own rows.

> ⚠️ **Why not let anyone read the page-view rows?** After a magic-link login, the page URL contains your access and refresh tokens (`#access_token=...`). An earlier version of this app recorded the full URL in a world-readable table — anyone with the publishable key could have read live sessions. "No secrets here" is a claim you have to keep proving as the app changes.

> 💡 **Ask your AI assistant**: *"Explain Row Level Security in PostgreSQL. Why is it more secure than checking permissions in application code?"*

### 🧪 Hands-On: See RLS in Action

After the script completes, try this experiment. Ask your AI assistant to help you write a quick Python snippet that uses **just the publishable key** (not the secret key) to:

1. **Call `page_view_count()`** → ✅ Should work! Anyone can see the total.
2. **Query `page_views` rows** → ❌ Should fail with **permission denied**! The anon role has no `SELECT` privilege.
3. **Query `sentence_embeddings`** → ❌ Should return **zero rows**! The privilege exists, but RLS filters out every row.

Here's a starting point:

```python
from supabase import create_client
import json

with open('config.public.json') as f:
    public = json.load(f)

# Connect with the publishable key (not the secret key!)
client = create_client(public['SUPABASE_URL'], public['SUPABASE_ANON_KEY'])

# This works — page_view_count() is executable by anon (the role the publishable key maps to)
count = client.rpc('page_view_count').execute().data
print(f"page_view_count(): {count} ✅")

# This fails — anon has no SELECT privilege on page_views (layer 1)
try:
    client.table('page_views').select('*').limit(5).execute()
except Exception as e:
    print(f"page_views rows: blocked by privileges 🔒 ({e})")

# This returns nothing — RLS filters every row for anon (layer 2)
result = client.table('sentence_embeddings').select('*').limit(5).execute()
print(f"sentence_embeddings: {len(result.data)} rows (expected: 0) 🔒")
```

> 💡 **Try it!** Seeing the output first-hand makes the RLS concept click.

> 🧪 **Prove per-user RLS too**: `scripts/sql/test_question_history_rls.sql` creates two throwaway users inside a transaction and checks that each sees only their own `question_history` rows, can't insert rows for the other, and can't update or delete. Paste it into the Supabase SQL editor; it ends with a `ROLLBACK`, so nothing is kept. Each check prints PASS or FAIL.

> 🤔 **Think about it**: The publishable key is in your `config.public.json`. Anyone can see it. So why is your data still safe?

### The Security Model

```
┌────────────────────────────────────────┐
│ Supabase-Managed Security              │
│                                        │
│  publishable key → RLS decides what you see   │
│  ┌─────────────────┐  ┌────────────┐  │
│  │ page_views      │  │ embeddings │  │
│  │ 🔢 public count │  │ 🔒 auth    │  │
│  │ 🌍 public write │  │   only     │  │
│  └─────────────────┘  └────────────┘  │
└────────────────────────────────────────┘

┌────────────────────────────────────────┐
│ Self-Built Security (Edge Functions)   │
│                                        │
│  OpenAI API key → Supabase can't       │
│  protect this for you — YOU build      │
│  the secure layer with Edge Functions  │
│  ┌──────────────┐  ┌──────────────┐   │
│  │ embed-       │  │ generate-    │   │
│  │ question     │  │ answer       │   │
│  │ 🔒 auth +    │  │ 🔒 auth +    │   │
│  │    API key   │  │    API key   │   │
│  └──────────────┘  └──────────────┘   │
└────────────────────────────────────────┘
```

**Key insight**: Supabase protects your *database* with RLS, but it can't protect *third-party API keys* like OpenAI. That's why you'll build Edge Functions — they're the secure intermediary you control.

## Verification

- [ ] Script prints "✅ Database schema created successfully!"
- [ ] Script prints "✅ Table verified" with a row count
- [ ] You can see `sentence_embeddings`, `question_history`, and the page-view tables in Supabase Dashboard → Table Editor
- [ ] (Optional) The RLS test snippet shows the page view count, a permission error for `page_views` rows, and 0 rows from `sentence_embeddings`
- [ ] (Optional) `scripts/sql/test_question_history_rls.sql` prints PASS for every check

## → Next: [Step 04: Scrape & Import Data](04_scrape_and_import.md)
