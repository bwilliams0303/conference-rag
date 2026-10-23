# Step 7: Make It Yours — 🚀 Personal Feature + Reflection

```
  ✅ LAUNCH ──→ ✅ SIGN IN ──→ ✅ KEYWORD ──→ ✅ SEMANTIC ──→ ✅ RAG ──→ ▶ 🚀 YOURS
                                                                          ~~~~~~~~
                                                                          YOU ARE HERE
```

## 🎓 What You Built

Congratulations! You built a production-ready RAG application from scratch.

### Technical Skills Practiced

✅ **Vector Embeddings** — Converted text to 1,536-dimensional vectors
✅ **Semantic Search** — Used pgvector for similarity search
✅ **RAG Architecture** — Combined retrieval + generation
✅ **Edge Functions** — Deployed serverless functions
✅ **Row Level Security** — Protected data with database-level policies
✅ **Production Deployment** — Full-stack app on GitHub Pages
✅ **AI-Assisted Development** — Used an AI coding assistant throughout

## 🚀 Add Your Own Feature

**Make this app yours.** Add an interesting feature that demonstrates understanding of the system.

### 📖 Example: Question History (already built)

To show what a complete feature looks like, this repo already includes one: **Question History**. The **🕘 My History** panel lists your past questions and answers. It touches every layer of the stack:

- **Database**: the `question_history` table (created by `01_create_schema.py`), with Row Level Security so each user can only read and insert **their own** rows. You can prove that with `scripts/sql/test_question_history_rls.sql`.
- **Frontend**: `app.js` saves each answer with `saveHistory()` and shows the list with `loadHistory()`.

Read it end to end (ask your AI assistant to trace a question from the input box into the table and back) and use it as a pattern for your own feature. Yours should be something different. If you need ideas:

- **Ask a specific speaker**: Filter RAG answers to only use talks from a specific speaker
- **Embedding caching**: Cache question embeddings to avoid re-calling OpenAI for repeated questions
- **Analytics dashboard**: Track popular questions and frequently matched talks
- **Compare search modes**: Show keyword, semantic, and RAG results side-by-side for the same query

Be creative! Have fun! This is your chance to show you understand the system well enough to extend it.

## 📝 Reflection Questions

Answer these in your submission. Use your AI assistant to discuss these — it can prompt you with follow-up questions to deepen your understanding.

### 1. Security Architecture ⭐⭐⭐⭐⭐

Draw or describe a diagram showing what protects each sensitive asset in this application. Your answer should address:
- Why is the publishable (anon) key safe to expose in `config.public.json`?
- What would happen if someone got the secret (service role) key, or your Supabase access token?
- How do Edge Functions protect the OpenAI API key, and what stops a signed-in user from running up your bill (think rate limits, sign-ups off, and why `generate-answer` takes talk ids instead of text)?
- What role does Row Level Security play?

### 2. Edge Functions & the "Secure Middle" ⭐⭐⭐⭐⭐

Why can't we call OpenAI directly from the browser? Explain the flow: browser → Edge Function → OpenAI. What role does JWT verification play in this chain? How does this pattern apply to other production applications?

### 3. From SQL to Semantics ⭐⭐⭐⭐

Compare keyword search (SQL `ILIKE`) vs semantic search (vector cosine similarity):
- When would each be better?
- Give a specific query example where one succeeds and the other fails
- What makes semantic search "understand" meaning?

### 4. RAG vs Fine-Tuning ⭐⭐⭐⭐

We used RAG instead of fine-tuning a model on conference talks. Research what fine-tuning is (this wasn't covered in class — go find out!). Then explain:
- What are the trade-offs between RAG and fine-tuning?
- Why did we choose RAG for this application?
- When might fine-tuning be the better choice?

### 5. AI-Assisted Development ⭐⭐⭐

Briefly describe how your AI coding assistant helped you during this assignment:
- What did it do well?
- Where did you need to guide it?
- What did you learn about working with AI tools?

## 🚀 Other Ideas for Extensions

Want to take this further? Try these challenges:

### Try Different Chunking
Compare sentence-level vs. paragraph-level chunking. Which gives better results for different types of questions?

### Build a Citation Viewer
When RAG returns an answer, link each citation to the original talk so users can read the full context.

### Add Streaming Responses
Instead of waiting for the full GPT response, stream it token-by-token for a ChatGPT-like experience.

## 📚 Resources

- [Supabase pgvector Guide](https://supabase.com/docs/guides/ai)
- [OpenAI Embeddings Guide](https://platform.openai.com/docs/guides/embeddings)
- [RAG Best Practices](https://weaviate.io/blog/rag-evaluation)
- [Chunking Strategies for RAG](https://www.superlinked.com/vectorhub/articles/chunking-vs-semantic-splitting)
- [Supabase Edge Functions Docs](https://supabase.com/docs/guides/functions)

## 🎓 Assignment Deliverables

1. **GitHub repository URL** (your fork)
2. **Live deployment URL** (GitHub Pages)
3. **Screenshot** of a working RAG query + answer
4. **Your custom feature** — describe what you added and why
5. **Written reflection** answering the questions above
