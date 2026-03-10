# Contributing to Open Model Arena

Thanks for contributing — glad you’re here.

## Ground Rules

- Keep PRs focused and small when possible.
- Prefer clarity over cleverness.
- Add or update tests for behavior changes.
- Update docs when config, endpoints, or UX behavior changes.

## Local Setup

```bash
git clone https://github.com/pete-builds/open-model-arena.git
cd open-model-arena
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp models.yaml.example models.yaml
cp .env.example .env
```

Set required env vars in `.env`:

- `ARENA_PASSPHRASE`
- `AUTH_TOKEN_SECRET`

## Run Tests

```bash
python -m pytest tests/ -v
```

## Pull Request Checklist

- [ ] Tests pass locally
- [ ] New behavior includes tests (or rationale why not)
- [ ] README/docs updated (if needed)
- [ ] No secrets added to code, docs, or commits

## Commit / PR Style

- Use descriptive commit messages.
- In PR descriptions, include:
  - problem statement
  - what changed
  - any risk or migration notes

## Reporting Security Issues

Please do **not** open public issues for security vulnerabilities.
See [SECURITY.md](./SECURITY.md) for responsible disclosure guidance.
