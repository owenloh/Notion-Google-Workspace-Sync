"""Google Docs read/write in terms of canonical Markdown."""

from __future__ import annotations

from app.connectors.google._retry import execute as _exec
from app.core.markdown import (
    clear_doc_requests,
    docs_document_to_markdown,
    markdown_to_docs_requests,
)


def read_markdown(docs, doc_id: str) -> str:
    document = _exec(docs.documents().get(documentId=doc_id))
    return docs_document_to_markdown(document)


def write_markdown(docs, doc_id: str, markdown: str) -> None:
    """Replace the entire Doc body with content rendered from ``markdown``."""
    document = _exec(docs.documents().get(documentId=doc_id))
    requests = clear_doc_requests(document) + markdown_to_docs_requests(markdown)
    if requests:
        _exec(docs.documents().batchUpdate(documentId=doc_id, body={"requests": requests}))
