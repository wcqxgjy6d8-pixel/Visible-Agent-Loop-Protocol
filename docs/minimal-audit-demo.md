# Minimal Audit Demo

This demo shows VALP's narrow value: a task is not done just because an agent or
runtime says it is done. Expected evidence must exist.

The demo uses the bundled Manual Mode example and does not launch a live
runtime.

## 1. Audit The Complete Example

```bash
git clone https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol.git
cd Visible-Agent-Loop-Protocol
python -m pip install -r requirements-dev.txt

bin/valp audit examples/minimal-task
```

Expected result:

```text
VALP audit: PASS
Summary: pass=13 warn=0 fail=0 skip=6
```

## 2. Break The Expected Evidence

Copy the task to a temporary directory, then remove the review evidence that the
receipt ledger expects.

```bash
demo_dir="$(mktemp -d /tmp/valp-demo.XXXXXX)"
cp -R examples/minimal-task "$demo_dir/minimal-task"
mv "$demo_dir/minimal-task/agents/manual-reviewer/review.md" \
  "$demo_dir/minimal-task/agents/manual-reviewer/review.md.bak"

bin/valp audit "$demo_dir/minimal-task"
```

Expected result:

```text
VALP audit: FAIL
Summary: pass=12 warn=0 fail=1 skip=6

[FAIL] expected_evidence: Expected evidence exists
  Missing expected evidence: agents/manual-reviewer/review.md
```

## 3. Restore The Evidence

```bash
mv "$demo_dir/minimal-task/agents/manual-reviewer/review.md.bak" \
  "$demo_dir/minimal-task/agents/manual-reviewer/review.md"

bin/valp audit "$demo_dir/minimal-task"
```

Expected result:

```text
VALP audit: PASS
Summary: pass=13 warn=0 fail=0 skip=6
```

## What This Proves

This proves the evidence discipline, not live automation. The repository can
detect that a task folder is missing expected proof, even when the latest
receipt says the manual result was attested.

Full Mode claims require stronger runtime proof: a real runtime submission
receipt, expected evidence, review/verification status, approval resolution when
needed, and final synthesis.
