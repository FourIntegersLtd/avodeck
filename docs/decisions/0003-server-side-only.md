# 0003 Everything goes through the server

Date: 2026-10-15 · Status: accepted

**Decision.** The browser talks only to FastAPI. Login is handled by FastAPI calling Supabase Auth server-side, with an httpOnly, Secure, SameSite=Strict cookie and CSRF tokens. Files upload in chunks through FastAPI and are streamed back by it. Dictation audio is relayed by FastAPI to Azure Speech. Progress comes from server-sent events. Tables are in a schema not exposed to Supabase's Data API.

**Exceptions.** The signature drawing pad, and the offline witness-box search (courtrooms may have no connection).

**Why.** Tokens can't be stolen by page scripts; no keys in the browser; one place enforces every rule.

**Trade-off.** Upload bandwidth runs through DigitalOcean; slightly slower uploads than direct-to-storage.
