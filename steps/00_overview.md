# 🎓 Conference RAG — Overview

## What You'll Build

A **Retrieval Augmented Generation (RAG)** application that lets users ask questions about General Conference talks using semantic search and AI-generated answers.

Your app has three search modes that **light up** as you complete each milestone:

```
🏁 LAUNCH ──→ 🔐 SIGN IN ──→ 🔍 KEYWORD ──→ 🧠 SEMANTIC ──→ 🤖 RAG ──→ 🚀 YOURS
  "I have       "I can         First           Second          All          Personal
   a site!"      log in!"      green           green          green!        feature
                                light!          light!
```

| Mode | What It Does | Lights Up When |
|------|-------------|-----------------|
| 🔍 **Keyword Search** | SQL `ILIKE` query on talk text | Conference data imported |
| 🧠 **Semantic Search** | Vector similarity search with pgvector | Embeddings generated + Edge Function deployed |
| 🤖 **Ask a Question (RAG)** | AI-generated answers with source citations | All Edge Functions deployed |

## Architecture

```
┌─────────────┐
│   Browser   │  Student asks question
│  (GitHub    │
│   Pages)    │
└──────┬──────┘
       │
       ├─── Supabase Auth (magic link login)
       │
       ├─── Edge Function: embed-question
       │         ↓ OpenAI API (server-side 🔒)
       │         ↓ Returns embedding vector
       │
       ├─── Supabase Database (pgvector)
       │         ↓ match_sentences()
       │         ↓ Returns similar sentences
       │         ↓ Grouped by talk, ranked
       │
       └─── Edge Function: generate-answer
                ↓ GPT-4o (server-side 🔒)
                ↓ Returns final answer
```

## What You'll Learn

1. **Vector Embeddings** — Representing text as searchable numbers
2. **Semantic Search** — Finding similar content with cosine similarity
3. **RAG Architecture** — Combining retrieval + generation
4. **Edge Functions** — Serverless compute for secure API management
5. **Row Level Security** — Database-level access control
6. **Production Deployment** — Full-stack app on GitHub Pages + Supabase

## Before You Start

**You'll need:**
- [GitHub](https://github.com) account
- [Supabase](https://supabase.com) account (free tier)
- [OpenAI API key](https://platform.openai.com/api-keys) (about $0.01–0.02 of embeddings, plus 1–2 cents per RAG answer)
- Python 3.9+ installed locally
- An AI coding assistant (pick one):
  - **Antigravity** (VS Code-based IDE) — [antigravity.google](https://antigravity.google)
  - **GitHub Copilot** (VS Code extension) — VS Code Marketplace
  - **Cursor** (standalone IDE) — [cursor.com](https://cursor.com)

> 💡 **Pro tip**: Throughout this assignment, whenever you’re stuck or curious, ask your AI assistant! For example:
> - *"What is a vector embedding?"*
> - *"Why do we use cosine similarity instead of Euclidean distance?"*
> - *"Explain what Row Level Security does in Supabase"*

## Cost & Time

- 💰 **About $0.01–0.02** for embeddings (~26,000 sentences), plus roughly **1–2 cents per RAG answer** (GPT-4o)
- 🛡️ Set a monthly budget limit on your OpenAI project (Step 05) so a bug or abuse can't surprise you
- ⏱️ **~2 hours** total across all steps

## Let's get started! → [Step 01: Fork & Deploy](01_fork_and_deploy.md)

> 🤖 **AI coding assistant?** Read [ai_agent_instructions.md](../ai_agent_instructions.md) for guidance on helping students with this assignment.
