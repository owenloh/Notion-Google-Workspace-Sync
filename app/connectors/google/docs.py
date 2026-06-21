"""Google Docs read/write in terms of canonical Markdown."""

from __future__ import annotations

from app.core.markdown import (
    clear_doc_requests,
    docs_document_to_markdown,
    markdown_to_docs_requests,
)

# A short header inserted at the top of every mirrored Doc, linking back to the
# Notion page. Kept out of the body hash by the engine (it is re-added on write
# and stripped on read), so it never triggers a phantom edit.
HEADER_PREFIX = "↩ Notion: "


def read_markdown(docs, doc_id: str) -> str:
    document = docs.documents().get(documentId=doc_id).execute()
    return docs_document_to_markdown(document)


def write_markdown(docs, doc_id: str, markdown: str) -> None:
    """Replace the entire Doc body with content rendered from ``markdown``."""
    document = docs.documents().get(documentId=doc_id).execute()
    requests = clear_doc_requests(document) + markdown_to_docs_requests(markdown)
    if requests:
        docs.documents().batchUpdate(documentId=doc_id, body={"requests": requests}).execute()
