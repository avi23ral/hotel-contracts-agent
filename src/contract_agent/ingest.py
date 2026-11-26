"""Turn any input (PDF, .eml, chat .txt) into plain text with a channel label.

Everything downstream works on text, so evidence quotes can be checked
against the exact text the model saw.
"""
from __future__ import annotations

from email import policy
from email.parser import BytesParser
from pathlib import Path

import pdfplumber
from pydantic import BaseModel


class SourceDoc(BaseModel):
    path: str
    channel: str  # pdf | email | chat
    text: str


def load_source(path: Path) -> SourceDoc:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        with pdfplumber.open(path) as pdf:
            pages = [f"[page {i}]\n{p.extract_text() or ''}" for i, p in enumerate(pdf.pages, 1)]
        return SourceDoc(path=path.name, channel="pdf", text="\n\n".join(pages))
    if suffix == ".eml":
        msg = BytesParser(policy=policy.default).parse(path.open("rb"))
        header = f"From: {msg['from']}\nTo: {msg['to']}\nDate: {msg['date']}\nSubject: {msg['subject']}\n\n"
        body = msg.get_body(preferencelist=("plain",)).get_content()
        return SourceDoc(path=path.name, channel="email", text=header + body)
    return SourceDoc(path=path.name, channel="chat", text=path.read_text())
