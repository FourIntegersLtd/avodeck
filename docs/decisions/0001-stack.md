# 0001 Stack: Vercel, FastAPI on DigitalOcean App Platform, Supabase

Date: 2026-10-15 · Status: accepted

**Decision.** Frontend on Vercel. FastAPI API and worker on DigitalOcean App Platform (London). Supabase (London) for Postgres + pgvector, Auth and Storage. AWS and Azure used only as AI APIs. Resend for patient-free email. Stripe for billing.

**Why.** No servers to manage or SSH into; a solo founder can run it. All patient-data services sit in London. Hosting cost is small next to AI cost.

**Rejected.** AWS EC2/ECS hosting (operational burden). Railway (no UK region as far as we know). Azure OpenAI UK South (limited UK capacity; zero retention hard to obtain).
