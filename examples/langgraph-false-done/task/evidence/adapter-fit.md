# LangGraph Adapter Fit

Result: PASS (6/6)

- Thread ID: provided by `POST /threads`.
- Submission/run ID: provided by `POST /threads/{thread_id}/runs`.
- Worker state: `pending` to `success` or `error` from the run API.
- Output/evidence refs: join output, thread state, and checkpoint refs are exported.
- Failure reason: join returns `__error__` with the runtime exception type and message.
- Restart/replay identity: a full local runtime stop/start restored thread
  `019f7e2f-c622-7203-b87f-3fab10c0c8fe` and checkpoint
  `1f184033-e3b4-6080-8001-7969014184b1`.

The fit used LangGraph `1.2.9`, LangGraph API `0.11.1`, and the local in-memory
development runtime with its on-disk development persistence. It is not a
production deployment claim.
