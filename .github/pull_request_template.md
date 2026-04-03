## What does this PR do?

Brief description of the change.

## Why?

What problem it solves or what it improves.

## How to test

```bash
# Commands to verify the change works
brainctl ...
python3 -m pytest tests/ -v
```

## Checklist

- [ ] `python3 -m py_compile src/agentmemory/_impl.py` passes
- [ ] `python3 -m pytest tests/` passes (102+ tests)
- [ ] New features have tests
- [ ] `brainctl --help` still looks clean
- [ ] README updated if user-facing behavior changed
