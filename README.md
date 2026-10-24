# QuiZ Bandiere

Quiz a risposta multipla sulle bandiere, in italiano e inglese. Due modalità:

- **Nazionali (FIFA)** — le 211 federazioni affiliate, con difficoltà 1–7.
- **Mondo** — tutte le 306 bandiere di flagcdn, con domande anche su colori e geometrie.

Disponibile come **versione web** (FastAPI + React) e come **app desktop macOS**
(bundle PyInstaller con launcher nativo).

## Requisiti

- Python 3.9+
- Node.js 18+ (per compilare il frontend)
- macOS 11+ solo per l'app desktop

## Setup

```bash
make setup
```

Crea `backend/.venv`, installa i requirements Python e le dipendenze npm.

### Dati e immagini delle bandiere

Il repository include i dati di base (`data/baseline`) e le cache di analisi
(`data/cache/*_analysis.json`), ma **non** le immagini PNG delle bandiere
(asset FIFA / flagcdn, non ridistribuiti). Scaricale una volta:

```bash
cd backend
.venv/bin/python scripts/update_data.py --source online      # 211 bandiere FIFA
.venv/bin/python scripts/build_world_data.py --force-flags   # 306 bandiere mondo
```

Le cache di analisi già presenti evitano di rianalizzare le immagini. Se
modifichi il codice in `backend/app/analysis/`, elimina
`data/cache/flag_analysis.json` e `data/cache/world_analysis.json` per forzare
la rigenerazione.

## Avvio (web)

```bash
# compila il frontend una volta
cd frontend && npm run build && cd ..

# avvia il server: serve API + frontend sulla stessa porta
make backend
```

Apri <http://127.0.0.1:8000>.

Per lo sviluppo dell'interfaccia con hot-reload:

```bash
make frontend   # Vite dev server
```

## App desktop macOS

```bash
cd packaging
export MACOSX_DEPLOYMENT_TARGET=11.0
../backend/.venv/bin/pyinstaller quiz_bandiere.spec --noconfirm \
  --distpath dist --workpath build
open "dist/QuiZ Bandiere.app"
```

L'app usa dati separati in `~/Library/Application Support/QuiZ Bandiere/data`
(leaderboard propria, nessuna interferenza con la versione web).

## Test

```bash
make test-backend        # pytest
cd frontend && npm run lint && npm run build
```

## Struttura

```
backend/app/
  core/        configurazione e logging
  data/        fetch, normalizzazione, validazione, manifest, asset
  analysis/    analisi colori/geometria/simboli delle bandiere (con cache)
  quiz/        motore domande, similarità, leaderboard, info paese, capocannonieri
  review/      report e coda di revisione manuale
  main.py      API FastAPI + serving del frontend compilato
frontend/src/  React + TypeScript (App.tsx, stili)
packaging/     launcher tkinter, spec PyInstaller, icona
data/          baseline, cache, leaderboard (i dati personali sono ignorati da git)
```

## Crediti e licenze

- Immagini bandiere: asset ufficiali FIFA e [flagcdn.com](https://flagcdn.com)
  (pubblico dominio), usate a scopo didattico.
- Dati paese: Wikipedia / Wikidata (CC BY-SA / CC0).
- Icona: emoji ⚽ [Twemoji](https://github.com/twitter/twemoji), CC BY 4.0.

Il codice non include ancora un file `LICENSE`: aggiungine uno prima di
pubblicare il repository.
