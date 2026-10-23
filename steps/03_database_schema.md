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
- **`page_views` table** — Records every page visit (🌍 **public write**, count-only read)
- **RLS policies + privileges** — Controls who can access each table
- **`match_sentences()` function** — A stored procedure for vector similarity search
- **`page_view_count()` function** — Returns the total for the page view counter

> 💡 **Ask your AI assistant**: *"What is pgvector and how does cosine similarity search work?"*

### Two Tables, Two Access Levels

| Table | Who can read? | Who can write? | Why? |
|-------|--------------|----------------|------|
| `page_views` | 🔢 Anyone, but only the total via `page_view_count()` | 🌍 Anyone (`page_url`, `user_agent` only) | Public analytics — visitors' rows stay private |
| `sentence_embeddings` | 🔒 Authenticated users only | 🔒 Service role only | Protected content — requires login |

This is **Row Level Security (RLS)** in action. Both tables have RLS enabled, but their *policies* define very different access rules.

### Understanding the Schema

```sql
-- sentence_embeddings: Protected data
CREATE POLICY "Allow authenticated users to read"
ON sentence_embeddings FOR SELECT
TO authenticated          -- ← Only logged-in users
USING (true);

-- page_views: Public writes, no public reads
CREATE POLICY "Allow public inserts"
ON page_views FOR INSERT
TO anon, authenticated    -- ← Anyone, even without login
WITH CHECK (true);
```

Postgres checks two layers, in order:

1. **Privileges** (`GRANT` / `REVOKE`) — can this role touch this table or column *at all*? If not, the request fails with `permission denied`.
2. **RLS policies** — of the rows the role may touch, *which ones*? Rows that fail the policy are silently filtered out.

The script uses both. For example, `page_views` grants `INSERT` on only `page_url` and `user_agent`, so a visitor can't backdate `visited_at`, and it has no `SELECT` policy at all. The counter calls `page_view_count()` instead, a `SECURITY DEFINER` function that runs as the table owner and returns only a number.

> ⚠️ **Why not let anyone read `page_views`?** After a magic-link login, the page URL contains your access and refresh tokens (`#access_token=...`). An earlier version of this app recorded the full URL in a world-readable table — anyone with the anon key could have read live sessions. "No secrets here" is a claim you have to keep proving as the app changes.

> 💡 **Ask your AI assistant**: *"Explain Row Level Security in PostgreSQL. Why is it more secure than checking permissions in application code?"*

### 🧪 Hands-On: See RLS in Action

After the script completes, try this experiment. Ask your AI assistant to help you write a quick Python snippet that uses **just the anon key** (not the service key) to:

1. **Call `page_view_count()`** → ✅ Should work! Anyone can see the total.
2. **Query `page_views` rows** → ❌ Should fail with **permission denied**! The anon role has no `SELECT` privilege.
3. **Query `sentence_embeddings`** → ❌ Should return **zero rows**! The privilege exists, but RLS filters out every row.

Here's a starting point:

```python
from supabase import create_client
import json

with open('config.public.json') as f:
    public = json.load(f)

# Connect with the ANON key (not service key!)
client = create_client(public['SUPABASE_URL'], public['SUPABASE_ANON_KEY'])

# This works — page_view_count() is executable by anon
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

> 🤔 **Think about it**: The anon key is in your `config.public.json`. Anyone can see it. So why is your data still safe?

### The Security Model

```
┌────────────────────────────────────────┐
│ Supabase-Managed Security              │
│                                        │
│  anon key → RLS decides what you see   │
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
- [ ] You can see both `sentence_embeddings` and `page_views` in Supabase Dashboard → Table Editor
- [ ] (Optional) The RLS test snippet shows the page view count, a permission error for `page_views` rows, and 0 rows from `sentence_embeddings`

## → Next: [Step 04: Scrape & Import Data](04_scrape_and_import.md)
