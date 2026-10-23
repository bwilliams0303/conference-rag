# Step 6: RAG — 🤖 All Lights Green!

```
  🏁 LAUNCH ──→ 🔐 SIGN IN ──→ ✅ KEYWORD ──→ ✅ SEMANTIC ──→ ▶ 🤖 RAG ──→ 🚀 YOURS
                                                                  ~~~~~~~
                                                                  YOU ARE HERE
```

## What You'll Learn
- What RAG (Retrieval Augmented Generation) is
- How to combine retrieval + generation for AI-powered answers
- How context windows work in LLMs
- How a second Edge Function turns retrieved context into an answer

## Why This Matters

This is the culmination of everything you've built. Keyword search finds exact words. Semantic search finds similar meanings. RAG goes further — it **reads** the most relevant talks and **generates** a natural-language answer with citations. This is how modern AI assistants (ChatGPT, Perplexity, etc.) work under the hood.

## What to Do

### 1. Read the generate-answer Edge Function

You need one more Edge Function: `generate-answer`. Like `embed-question`, it **already exists** in the repo at `supabase/functions/generate-answer/index.ts`. Read it and make sure you understand it (ask your AI assistant to explain it), rather than writing it from scratch. Here is what it does, described as a spec:

> 🤖 **The function**: *a Supabase Edge Function called `generate-answer`. It:*
> - *Accepts a POST request with `{ "question": "...", "talk_ids": [...] }` (up to 3 talk ids), or `{ "ping": true }` as a free readiness check*
> - *Fetches those talks' text itself, using the signed-in user's own token (so RLS applies)*
> - *Caps the context: 8,000 characters per talk and 20,000 in total*
> - *Calls GPT-4o (`max_tokens` 600) with a prompt that tells it to cite the talks it draws from, and to say so when the talks don't answer the question*
> - *Returns `{ "answer": "...", "sources": [{ "talk_id", "title", "speaker", "url" }] }`*
> - *Verifies the user, handles CORS, and enforces a per-user rate limit of 5 per minute and 50 per day*

> 🤔 **Why doesn't the browser send the talk text?** Because anything the browser sends can be faked. If the function accepted the text, anyone could paste in arbitrary content: a prompt-injection attack ("ignore your instructions and..."), or just a huge essay that **you** pay GPT-4o to read. Sending only talk ids means the function reads the text from your own database, and only the talks you imported can ever reach the prompt.

> 💰 **Cost**: each answer is roughly 1–2 cents (up to about 5,000 input tokens plus 600 output tokens on GPT-4o). The caps and rate limit keep that bounded.

### What's Happening Behind the Scenes

When you ask a question, the full RAG pipeline executes:

```
Your Question: "How can I find peace during hard times?"
    ↓
1. embed-question Edge Function
    → Sends question to OpenAI
    → Returns 1,536-dimensional embedding
    ↓
2. Vector Search (match_sentences)
    → pgvector finds 20 most similar sentences
    → Groups by talk_id, ranks by relevance
    → Returns top 3 talks
    ↓
3. generate-answer Edge Function
    → Browser sends the question + the 3 talk ids (not the text)
    → Function fetches those talks' sentences itself (RLS applies)
    → Caps the context, sends question + talks to GPT-4o
    → Returns a natural language answer + the list of sources
    ↓
4. Display in UI ✨
    ↓
5. Save the Q&A to your question history (Step 07)
```

> 💡 **Ask your AI assistant**: *"What is RAG and why is it better than fine-tuning for this use case?"*

### 2. Deploy the Function

```bash
supabase functions deploy generate-answer --project-ref YOUR_PROJECT_REF --use-api
```

> ⚠️ **Same auth pattern as before**: `config.toml` keeps `verify_jwt = true`, so the platform checks the JWT first and the function then confirms it's a signed-in user. Your `OPENAI_API_KEY` secret and the `ALLOWED_ORIGINS` edit from Step 05 already cover this function too.

### 3. All Three Lights Green! 🟢🟢🟢

After deploying:

1. **Refresh your site** (hard refresh: Ctrl+Shift+R)
2. All three panels should show **🟢 Ready**!

Try asking: *"What have church leaders taught about prayer?"*

You should get an AI-generated answer with source citations!

### Troubleshooting

| Issue | Solution |
|-------|----------|
| Search shows "Not Ready" | Make sure you completed the relevant step (data import, edge function deployment) |
| "Failed to get embedding" | Check Edge Function deployment and that OPENAI_API_KEY secret is set |
| No search results | Verify data was imported (check row count in Supabase Dashboard) |
| Login doesn't work | Check redirect URL in Supabase Auth settings |
| "Too many questions" (429) | You hit the rate limit (5 per minute, 50 per day). Wait a minute and try again |
| Browser says CORS error | Check `ALLOWED_ORIGINS` in `supabase/functions/_shared/cors.ts`, then redeploy both functions |
| Changes don't appear | Hard refresh (Ctrl+Shift+R) or try incognito window |

## Verification

- [ ] `generate-answer` function deployed without errors
- [ ] All three search panels show **🟢 Ready**
- [ ] Keyword search returns relevant results
- [ ] Semantic search returns results even for non-exact-match queries
- [ ] RAG returns an AI-generated answer with talk citations
- [ ] You can explain why the browser sends talk ids instead of talk text
- [ ] 📸 **Take a screenshot** of a working RAG query — you'll need this for submission!

## 🎉 Milestone: All lights green! Your RAG app is complete!

> 💾 **Commit your work!** `git add -A && git commit -m "All search modes working" && git push`

## → Next: [Step 07: Make It Yours](07_make_it_yours.md)
