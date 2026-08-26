# {{adapter_name}} VALP Adapter

This starter isolates runtime-specific API calls from VALP evidence decisions.
Implement the `RuntimeClient` methods in `adapter.py`, then keep the supplied
result mapping unchanged unless your runtime exposes stronger evidence.

The required adapter contract is:

1. `adapter.json` implements the ABI 1.0 closed capability table for `probe`,
   `submit`, `observe`, `cancel`, `resume`, and `prove`.
2. `submit` returns a real runtime submission ID and replay/thread identity.
3. `get_run` returns runtime-owned state and a concrete failure reason.
4. A local observation timeout returns `waiting`; it never cancels the worker.
5. Runtime success with missing expected evidence returns `blocked`.
6. `completed` is emitted only when every expected task-local ref exists and is
   non-empty.
7. Submission, output, state, and failure records are persisted before a VALP
   terminal receipt is appended.

Run the included contract tests:

```bash
python3 -m unittest test_adapter.py
```

Next, replace `FakeRuntimeClient` in the tests with your runtime client and add
one live false-done case. Preserve the first failed receipt, repair on the same
replay identity when supported, run an independent review, and finish with
`valp audit ... fail_count=0`.
