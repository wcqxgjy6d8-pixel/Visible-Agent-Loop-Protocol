# Verification

Result: passed.

Verification commands for this case study:

```bash
scripts/verify-examples.sh
python3 -m valp_cli audit examples/real-doc-calibration-task
```

The first command validates JSON and JSONL examples, validates bundled example
JSON against schemas, runs unit tests, and audits the bundled task examples.
The second command audits this real documentation calibration case study.

This evidence verifies repository structure and auditability. It does not
verify live runtime dispatch.
