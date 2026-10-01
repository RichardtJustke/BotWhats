import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hashlib
import re
import chromadb
import httpx

from app.core import config

KNOWLEDGE_BASE_DIR = Path(__file__).resolve().parent.parent / "knowledge_base"
CHROMA_DIR = Path(__file__).resolve().parent.parent / "chroma_data"

REQUEST_DELAY = 0.2
MAX_RETRIES = 5

chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))
collection = chroma_client.get_or_create_collection("knowledge_base")

http_client = httpx.Client(timeout=60.0)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
    if not match:
        return {}, text

    raw_meta, body = match.groups()
    meta = {}
    for line in raw_meta.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return meta, body


def chunk_by_headers(body: str) -> list[str]:
    sections = re.split(r"\n(?=## )", body)
    return [s.strip() for s in sections if len(s.strip()) > 30]

def embed(text: str) -> list[float]:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = http_client.post(
                "https://openrouter.ai/api/v1/embeddings",
                headers={"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"},
                json={"model": "openai/text-embedding-3-small", "input": text},
            )
            resp.raise_for_status()
            time.sleep(REQUEST_DELAY)
            return resp.json()["data"][0]["embedding"]
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 429 and e.response.status_code < 500:
                raise  # erro do cliente (401, 400...) não é transitório, não adianta tentar de novo
            if attempt == MAX_RETRIES:
                raise
            wait = 10 * attempt
            print(f"    Erro {e.response.status_code}, tentativa {attempt}/{MAX_RETRIES}, esperando {wait}s...")
            time.sleep(wait)
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            if attempt == MAX_RETRIES:
                raise
            wait = 10 * attempt
            print(f"    Erro {type(e).__name__}, tentativa {attempt}/{MAX_RETRIES}, esperando {wait}s...")
            time.sleep(wait)
    raise RuntimeError("Esgotou tentativas de embedding")


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def indexed_hashes() -> dict[str, str | None]:
    """id do chunk -> hash do conteúdo indexado (None em chunks de antes do hash)."""
    existing = collection.get(include=["metadatas"])
    return {
        chunk_id: (meta or {}).get("content_hash")
        for chunk_id, meta in zip(existing["ids"], existing["metadatas"])
    }


def ingest():
    md_files = list(KNOWLEDGE_BASE_DIR.rglob("*.md"))
    print(f"Encontrados {len(md_files)} arquivos .md")

    indexed = indexed_hashes()
    print(f"Já indexados anteriormente: {len(indexed)} chunks (só os alterados serão refeitos)\n")

    total_new = 0
    seen_ids: set[str] = set()

    for filepath in md_files:
        text = filepath.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(config.fill_placeholders(text))
        chunks = chunk_by_headers(body)
        tema = filepath.parent.name

        file_new = 0
        for i, chunk in enumerate(chunks):
            chunk_id = f"{filepath.relative_to(KNOWLEDGE_BASE_DIR)}-{i}".replace("/", "_")
            seen_ids.add(chunk_id)
            chunk_hash = content_hash(chunk)
            # Editar um .md (ex: corrigir telefone) muda o hash e força reindexar
            if indexed.get(chunk_id) == chunk_hash:
                continue

            embedding = embed(chunk)

            collection.upsert(
                ids=[chunk_id],
                embeddings=[embedding],
                documents=[chunk],
                metadatas=[{
                    "source": str(filepath.relative_to(KNOWLEDGE_BASE_DIR)),
                    "tema": tema,
                    "title": meta.get("titulo") or meta.get("title") or filepath.stem,
                    "content_hash": chunk_hash,
                }],
            )
            file_new += 1
            total_new += 1

        if file_new:
            print(f"  {filepath.relative_to(KNOWLEDGE_BASE_DIR)}: {file_new} chunks novos/alterados")

    # Arquivo apagado (ou que ficou com menos seções) não pode continuar respondendo
    stale_ids = sorted(set(indexed) - seen_ids)
    if stale_ids:
        collection.delete(ids=stale_ids)
        print(f"\nRemovidos {len(stale_ids)} chunks de conteúdo que não existe mais")

    print(f"\nTotal novo/alterado indexado nessa rodada: {total_new}")
    print(f"Total geral na coleção: {collection.count()}")


if __name__ == "__main__":
    ingest()