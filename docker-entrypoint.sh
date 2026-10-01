#!/bin/sh

echo "Sincronizando base de conhecimento (pula chunks já indexados)..."
python scripts/ingest.py || echo "Aviso: ingestao da base de conhecimento falhou, seguindo com o que ja estiver indexado"

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
