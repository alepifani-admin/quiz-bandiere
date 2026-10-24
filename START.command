#!/bin/bash
# START — avvia Quiz Bandiere (backend + apertura browser)
cd "$(dirname "$0")"

PORT=8000
URL="http://127.0.0.1:$PORT"

if [ ! -x backend/.venv/bin/uvicorn ]; then
  echo "ERRORE: backend/.venv non trovato. Esegui prima: make setup"
  read -r -n 1 -s -p "Premi un tasto per chiudere..."
  exit 1
fi

# Avvia il backend solo se non è già in ascolto sulla porta
if ! curl -s -o /dev/null "$URL/api/flags"; then
  echo "Avvio del backend su $URL ..."
  (cd backend && nohup .venv/bin/uvicorn app.main:app --port "$PORT" --log-level warning > /tmp/quiz-backend.log 2>&1 &)
  for _ in {1..30}; do
    sleep 1
    curl -s -o /dev/null "$URL/api/flags" && break
  done
else
  echo "Backend già in esecuzione su $URL"
fi

if curl -s -o /dev/null "$URL/api/flags"; then
  echo "Quiz Bandiere pronto: $URL"
  open "$URL"
else
  echo "ERRORE: il backend non risponde. Controlla /tmp/quiz-backend.log"
fi

echo
read -r -n 1 -s -p "Premi un tasto per chiudere..."
