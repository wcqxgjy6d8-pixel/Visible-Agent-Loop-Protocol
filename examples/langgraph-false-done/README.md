# LangGraph False-Done Reproduction

This is the first real non-HERDR VALP tracer bullet. A LangGraph API worker
returned runtime `success` and claimed that a report had been generated, while
the expected report and worker evidence files did not exist. VALP preserved the
run output and appended `dispatch_blocked`. A repair run reused the same thread,
created both refs, and a separate reviewer run verified the result.

Audit the complete sanitized task:

```bash
bin/valp audit examples/langgraph-false-done/task
```

Expected summary:

```text
PASS: pass=26 warn=0 fail=0 skip=2
```

Re-run the live false-done, repair, and independent review path:

```bash
examples/langgraph-false-done/reproduce.sh
```

The script creates an isolated temporary workspace, starts the local LangGraph
API, records a real run ID, confirms the first adapter command exits blocked,
preserves that workspace's failing audit, repairs on the same thread identity,
runs the reviewer on a separate run, and audits the newly reproduced task to
`fail_count=0`. It leaves the temporary workspace in place for inspection and
stops only the server process that it started. It requires `uv`, Python 3.12,
and `curl`; the script creates an ignored Python 3.12 environment on first run.

The case-local compatibility level in `conformance.json` is descriptive, not a
new protocol-wide conformance grade. It applies only to this adapter/runtime
pair.
