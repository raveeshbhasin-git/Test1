# Deploying Ignition Watch

Ignition Watch runs inside the existing **themes_web** Railway service
(`alert-youthfulness`, project `reasonable-forgiveness`). It deploys from
`raveeshbhasin-assistant/momentum-scanner` `main`, so there is no Railway
setup to do. The patch in this folder is that change, ready to apply:

```bash
cd C:\dev\Trader-v3          # canonical local repo
git fetch origin && git status -sb
curl -L -o ignition.patch https://raw.githubusercontent.com/raveeshbhasin-git/Test1/claude/stock-momentum-patterns-op28f6/momentum/deploy/0001-themes_web-v1.5.0-ignition-watch.patch
git am ignition.patch
python -m pytest tests/ -q
git push origin main         # Railway auto-deploys
```

Live at: https://alert-youthfulness-production-354a.up.railway.app/ignition
(the first scan runs ~2 min after deploy, then weekdays 17:00 ET).
