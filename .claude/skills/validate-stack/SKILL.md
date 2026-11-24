---
name: validate-stack
description: Run the stack-spec local validator on this repository (files, komuta.yaml shape, placeholders, secret leak scan) and fix what it reports. Use before committing, publishing or deploying.
---

# Validate the stack

Required: the `komuta-stack-spec` repository (its path is `<stack-spec>` below) and PyYAML.

```bash
python -m pip install -r <stack-spec>/requirements.txt      # PyYAML only
python <stack-spec>/tools/validate_stack.py .                # this repo
python <stack-spec>/tools/validate_stack.py komuta.yaml      # manifest only
python <stack-spec>/tools/validate_stack.py . --strict       # warnings count as errors too
python <stack-spec>/tools/validate_stack.py . --format json  # machine-readable output
```

- At the end of the output you must see `GECTI: 0 hata, ...`; exit code 0.
- `<...>` placeholders are accepted with `--allow-placeholders` only for templates/examples; do not use them in a repo that will be published.
- If the secret scan reports a finding, delete the value from the file and provide it on Komuta with `secrets` + `valueFrom.secret`; if it was committed to history, rotate the secret.
- Then the tests: `python -m pytest -q tests` and the bridge/plugin state: `python -m stack_kit doctor`.

This is a local pre-check; for Komuta's own validation move on to the `deploy-to-komuta` skill.
