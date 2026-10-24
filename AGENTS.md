# QuiZ Bandiere — Note per l'agente

Il repo produce DUE deliverable:
1. **Versione web**: backend FastAPI (uvicorn) + frontend (vite) servito dal backend.
2. **App desktop macOS**: bundle PyInstaller in `packaging/dist/QuiZ Bandiere.app`.

## Regola obbligatoria: app desktop
**OGNI fix o modifica apportata a `backend/` o `frontend/` deve essere SEMPRE
inclusa anche nell'app desktop `.app`.** Mai consegnare un fix solo per il web:
dopo ogni modifica ricompilare il bundle e rigenerare lo zip.

## Regola obbligatoria: pubblicazione su GitHub
**OGNI aggiornamento o modifica deve essere SEMPRE committato e pushato su
GitHub a fine lavoro.** Mai consegnare modifiche solo in locale.
Repo privato: <https://github.com/alepifani-admin/quiz-bandiere> (branch `main`).

```bash
cd "/Users/alessandro/Documents/QuiZ Bandiere"
git add -A
git diff --cached --name-only   # verificare: niente .venv/node_modules/dist/build/.env/dati personali
git -c user.name="alepifani-admin" \
    -c user.email="325175995+alepifani-admin@users.noreply.github.com" \
    commit -m "Descrizione breve della modifica"
git push origin main
```
- GitHub CLI locale: `.tools/gh/bin/gh` (ignorata da git); l'accesso è nel portachiavi macOS (`gh auth status` per verificare).
- Il bundle `.app` e lo `.zip` restano locali in `packaging/dist/` (ignorato da git); su GitHub va il codice sorgente e i dati di baseline.
- Non pubblicare (già in `.gitignore`): `.venv/`, `node_modules/`, `dist/`, `build/`, `.tools/gh/`, `.env`, `data/leaderboard.json`, `data/cache/country_info/`, `data/cache/*-flags/`, `data/cache/flag_views.json`.

## Avvio versione web (dev)
```bash
cd backend
nohup .venv/bin/uvicorn app.main:app --port 8000 --log-level warning >/tmp/quiz-visual/uvicorn.log 2>&1 &
```
- Il backend serve i file statici da `frontend/dist` (nessun fallback SPA: `/flags` → 404, navigare via link client-side).
- Il server legge i file dal disco: una nuova `npm run build` è servita subito, senza riavviare uvicorn.

## Build frontend
```bash
export PATH="/Users/alessandro/Documents/QuiZ Bandiere/.tools/node/bin:$PATH"
cd frontend && npm run lint && npm run build
```

## Rebuild app desktop (dopo OGNI modifica a backend/frontend)
```bash
cd packaging
export MACOSX_DEPLOYMENT_TARGET=11.0
"/Users/alessandro/Documents/QuiZ Bandiere/backend/.venv/bin/pyinstaller" quiz_bandiere.spec --noconfirm --distpath dist --workpath build
rm -f "dist/QuiZ Bandiere.app.zip"   # se bloccato dalla sandbox usare shutil / il tool Write
ditto -c -k --sequesterRsrc --keepParent "dist/QuiZ Bandiere.app" "dist/QuiZ Bandiere.app.zip"
```
- Icona app: palla da calcio bianca/nera (Twemoji ⚽, CC BY 4.0). Se cambia:
  `python build_icon.py` in `packaging/` rigenera `icon.icns` da `soccer-ball.png` (fare PRIMA del pyinstaller). Favicon web: `frontend/public/favicon.svg` (copiata in dist dal build vite).
- Il `.spec` raccoglie `../frontend/dist` e `../data` come datas; `app.*` finisce nel PYZ; `REPO_ROOT` in modalità frozen = `sys._MEIPASS` (vedi `backend/app/core/config.py`).
- Verifica minimale dopo il rebuild: avviare `dist/QuiZ Bandiere.app/Contents/MacOS/QuiZ Bandiere` in background, scoprire la porta con `lsof -nP -iTCP -sTCP:LISTEN -a -p <pid>`, controllare `/api/health`, `/` (HTML del gioco) e `/assets/<hash>.js` → 200. (L'avvio richiede ~10s.)
- I dati dell'app sono separati dal web: `~/Library/Application Support/QuiZ Bandiere/data` (leaderboard.json e fifa_quiz.db freschi, seed copiato da `data/`).

## Dati versione web
- Leaderboard: `data/leaderboard.json` (leggibile a ogni richiesta, niente cache persistente; `best_per_level` ha TTL 5s).
- `data/fifa_quiz.db`: attualmente 0 byte, nessuna tabella.
- Azzerare la leaderboard = riscrivere `data/leaderboard.json` con `{"version": 1, "scores": []}`.

## Test browser (regressione)
Harness puppeteer in `/var/folders/.../opencode/puppeteer-test/` (dir temporanea, reinstallare `puppeteer-core` se svuotata).
- Browser: `/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge`.
- `timeout-regression.js <porta>`: verifica che dopo il tempo scaduto la domanda successiva riparta con countdown pieno (fix errore 1).
- Flusso quiz: kind → `btn-hero.it` → mode → `count-btn` → nickname (`nick-input`, `flag-pick-btn`, `flag-pick-item`, `next`) → lingua `btn-hero.it` → difficoltà `level-btn`.
