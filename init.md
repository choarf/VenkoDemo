# init.md — setting up a workstation

Checklist to go from a fresh machine to one that can test, run and deploy VenkoDemo. `README.md` stays the full operator guide; this file is only the first-time setup.

## 1. Clone both repos side by side

The tools reach the gateway repo through `../Akvo_Green`, so the two must be siblings:

```bash
mkdir -p ~/git_reps/claude/projects/Venko && cd ~/git_reps/claude/projects/Venko
git clone https://github.com/choarf/VenkoDemo.git VenkoDemo
git clone git@github.com:choarf/Akvo_Green.git   Akvo_Green
```

Gateway certificates (`Akvo_Green/sites/<key>/certs/`) are git-ignored; copy them separately if you need to provision or push to a Pi.

## 2. Install the tools

| Tool | Why |
|---|---|
| Python 3.14 | Matches the Lambda runtime (`sam build` requires it) |
| AWS CLI v2 | Credentials, `create-deployment`, IoT commands |
| AWS SAM CLI | Build and deploy the stacks |
| bash, ssh | `tools/deploy_web.sh`, `tools/push_site.sh` |

## 3. Python environment

```bash
cd VenkoDemo
python3 -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt boto3 pytest
```

## 4. Check it works (no AWS needed)

```bash
cd backend && python -m pytest -q && cd ..
python3 tools/mock_api.py          # dashboard with SYNTHETIC data → http://localhost:8787
```

## 5. AWS access

```bash
aws configure                      # region us-east-1 (same as the gateway's IoT endpoint)
aws sts get-caller-identity        # must show the project account
aws cloudformation describe-stacks --stack-name venko-cmd4 --query 'Stacks[0].StackStatus'
```

Credentials need rights to create IAM roles (SAM deploy). Per-site parameters live in `infra/samconfig.toml`; there is no default section, so always pass `--config-env <key>`.

## 6. Per-site sanity check

```bash
python3 tools/site_summary.py <key>       # writes docs/sites/<key>.md with URLs and live status
python3 tools/push_shadow.py <key> --status   # shadow-enabled sites only (akvo01002)
```

Current sites: `cmd4`, `akvo01005`, `akvo01002`.

## 7. Claude Code

Start `claude` from the parent folder `~/git_reps/claude/projects/Venko` so both repos are readable, or from VenkoDemo (its `.claude/settings.local.json` adds `../Akvo_Green`). From inside Akvo_Green, run:

```
/add-dir /home/chris/git_reps/claude/projects/Venko/VenkoDemo
```

## Working from two computers (e.g. work and home)

Claude Code sessions are per computer; only what is in git is shared.

- **Same paths on both**: clone to `~/git_reps/claude/projects/Venko/` everywhere. Claude's per-project memory folder is keyed by the absolute path, and `.claude/settings.local.json` holds an absolute path to Akvo_Green (if the paths differ, run `/add-dir` once on that computer).
- **Routine**: `git pull` in both repos before starting a session; commit and push both before leaving. Unpushed work doesn't exist on the other computer.
- **Shared knowledge goes in `CLAUDE.md`** (committed), not in Claude's local memory or chat history, which stay on one computer.
- **Handoff**: conversations can't be resumed across computers. Push the work with clear commit messages (or a short note) and start the next session with "read the last commits and continue …".
- **Not in git, set up per computer**: AWS credentials (`aws configure`), gateway certificates (`Akvo_Green/sites/<key>/certs/`), personal `~/.claude/` settings (sync with a dotfiles repo if wanted, but never the whole `~/.claude/` folder: it holds credentials).

## Gotchas to know before the first commit

- `infra/.aws-sam/` and `__pycache__/` are still tracked and show as modified after builds/tests — stage files by name, never `git add -A`.
- `web/app-config.js` and `build/` are git-ignored; `deploy_web.sh` writes the per-site `app-config.js` straight to the site's web bucket.
- A change to `web/` must be deployed to **every** site.
- Commit VenkoDemo and Akvo_Green separately.

Next steps: README "How to deploy" for an existing site, README "Step by step: from a blank SD card…" for a new one.
