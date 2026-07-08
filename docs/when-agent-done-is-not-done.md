# When Agent "Done" Is Not Done

Agent demos usually end at the wrong moment.

The interesting part is not when an agent says:

```text
done
```

The interesting part is what happens next:

```text
Where is the proof?
```

VALP exists for that gap. It turns "done" into a small evidence chain that
another person, agent, or CI job can inspect.

## The Smallest Demo

Start with the bundled Manual Mode task:

```bash
git clone https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol.git
cd Visible-Agent-Loop-Protocol
python -m pip install -r requirements-dev.txt
bin/valp audit examples/minimal-task
```

Expected result:

```text
VALP audit: PASS
Summary: pass=13 warn=0 fail=0 skip=7
```

Now copy the task and remove the expected review evidence:

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

Restore the evidence:

```bash
mv "$demo_dir/minimal-task/agents/manual-reviewer/review.md.bak" \
  "$demo_dir/minimal-task/agents/manual-reviewer/review.md"

bin/valp audit "$demo_dir/minimal-task"
```

Expected result:

```text
VALP audit: PASS
Summary: pass=13 warn=0 fail=0 skip=7
```

## What This Shows

The task did not fail because a model was weak. It failed because the expected
proof was missing.

That is the core VALP rule:

```text
Runtime completed != VALP done
```

Completion needs:

- visible dispatch;
- receipt state;
- expected evidence;
- verification or review;
- approval when the task is high risk;
- final synthesis that cites proof.

## What To Critique

If this protocol is useful, it should catch failures that real agent workflows
actually produce. The best critique is a concrete case:

- an agent said done but no file changed;
- a runtime marked completed before evidence existed;
- text was inserted into a pane but not submitted;
- review advice was ignored by the coordinator;
- a queue job finished but no expected refs were written.

Post examples or objections here:

- [RFC: Phase 0 public evaluation](https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol/discussions/8)
- [Runtime adapter checklist feedback](https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol/discussions/9)

Start with the [failure gallery](failure-gallery.md) or the
[runtime adapter checklist](adapter-checklist.md).
