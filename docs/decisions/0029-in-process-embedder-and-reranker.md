# 0029 In-process embedder and reranker

Date: 2026-10-31 · Status: accepted · Decided by: the founder (spike 4(a), options "relax to throughput" and "smaller reranker")

**Problem.** Page embeddings (T-07c candidates, T-11 links) used a hashing stand-in. A real embedder was needed that keeps rule 5 (no data leaves the cell) and rule 7 (identifiers stripped first). The Railway worker holds no data at rest and has no volumes.

**Decision.**
- The embedder is `bge-small-en-v1.5` int8 ONNX, run in the worker process with `onnxruntime` and `tokenizers` (no torch). It gives 384 dimensions, so `page_embeddings` (vector(384), migration 0113) needs no migration; the model name `bge-small-en-v1.5-onnx-int8` differs from the stand-in, so `pages_to_embed` re-embeds on its own. `EMBEDDER=onnx` selects it. Search questions get the bge query prefix (`embed_queries`); pages and facts do not. Text longer than 512 tokens is cut to its **first 512-token window**: simpler than averaging windows, and a page's opening carries its date, source and heading.
- The reranker is `ms-marco-MiniLM-L-6-v2` int8 ONNX (`RERANKER=onnx`, `core/ai/rerank.py`), implementing `understand/links.py` `Reranker`. The nearest 50 facts are scored and the best 10 kept (`links.RERANK_POOL`). It sees identifier-stripped text only (`understand/jobs.py` passes the stripped wording). The larger `bge-reranker-base` was tried in the spike and rejected: about 760 MB alone.
- Weights are baked into the image. `api/models.lock.json` pins each file by Hugging Face revision and SHA-256; the `models` stage of `api/Dockerfile` runs `scripts/fetch_models.py` into `/opt/models` and the runtime copies it. Nothing is downloaded at run time. Models load on first use.
- Both Xenova ONNX exports are Apache-2.0 / MIT licensed conversions of the BAAI and cross-encoder originals; the files are pinned, so an upstream change cannot alter them.

**Why the bar changed from 150 ms per batch of 32 to throughput for pages.** Spike 4(a) (docs/spikes/README.md) showed 123 ms per 32 for short texts but 545 ms at 106 tokens. Pages are embedded by a background job, not while someone waits, so the right measure is time per bundle. Measured in a container limited to 1 GB and 4 CPUs: 4,177 ms per 32 full 512-token pages (131 ms a page), so **a 4,000-page bundle embeds in about 9 minutes**; one page at a time (what the pipeline does today) is 129 ms, the same. The 150 ms bar still holds where someone waits: a question embeds in 14 ms (4 questions), 32 short facts in 49 ms, and reranking 50 candidates in 77 ms.

**Memory.** Both models loaded in one process peak at 670 MB in the 1 GB container. `core/ai/onnx.py` limits each forward pass to 4,096 tokens (rows times the longest row): 32 full pages in one pass was killed at 1 GB, because attention memory grows with length squared.

**Image.** The runtime image grows by about 97 MB on disk (models 56 MB, `onnxruntime` 32 MB, `tokenizers` 9 MB); total image 291 MB.

**Quality.** `make eval` gains `--task links` (recall@10 over 10 questions of the synthetic bundle, `evals/.../expected/links.json`): stand-in 0.43, bge-small 0.58, bge-small plus reranker 0.55. The floor is 0.5 (`docs/decisions/deferred.md`). The reranker does not help on this bundle; it stays optional (`RERANKER` unset keeps the embedding order).

**Consequences.** Slice D's `understand/services.py` reads `RERANKER`; `understand/jobs.py` embeds questions with `embed_queries` and hands the reranker stripped text. Slice A is unchanged: its page embedding loop already calls `embedder.embed`. Local runs and CI use the stand-in unless `EMBEDDER=onnx` and `MODELS_DIR` are set; CI caches the model files and runs the model tests.
