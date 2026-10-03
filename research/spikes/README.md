# Spike code (raw, throwaway-quality, kept as evidence)
Run from this folder. Needs: Python 3.11+, `pip install playwright langgraph langgraph-checkpoint-sqlite`, system Chrome, gcloud auth (Vertex) for the LLM calls.
- probe.py — fields/CAPTCHA presence on 3 real ATS pages (no LLM)
- spike.py — S1: LLM plans, Playwright fills, read-back verifies; never submits. `SPIKE_MODEL=gemini-2.5-flash python spike.py [greenhouse|lever|ashby]`
- resume_test.py — S2: `a` fill+exit, `b` reattach/edit/reload/replay (launches Chrome on port 9333)
- lg_test.py — S3: LangGraph interrupt + SqliteSaver across process exit
Hard-coded Vertex project id and Windows Chrome path are spike-only; real code reads config from env.
