"""S3：Word 锁缩小（01 §5.5，用例 26–28；mock COM，Linux 可跑）。"""

from contextlib import contextmanager
import threading
import time
from types import SimpleNamespace

import pytest

from app import documents
from tests.test_documents import create_document, document_context  # noqa: F401


@pytest.fixture
def fake_word(monkeypatch):
    calls = []

    @contextmanager
    def fake_document(path, *, read_only=True):
        def export(target, _format):
            calls.append(str(path))
            time.sleep(fake_document.delay)
            with open(target, "wb") as handle:
                handle.write(b"%PDF-1.4 fake")
        yield SimpleNamespace(ExportAsFixedFormat=export, Save=lambda: None)

    fake_document.delay = 0.0
    monkeypatch.setattr(documents, "_word_available", lambda: True)
    monkeypatch.setattr(documents, "_local_word_document", fake_document)
    monkeypatch.setattr(documents, "_refresh_word_contents", lambda opened: None)
    return calls, fake_document


def _url(document):
    return f"/api/v1/documents/{document['id']}/word-preview"


def test_cache_hit_returns_while_com_slot_is_busy(document_context, fake_word):  # noqa: F811
    client = document_context[0]
    document = create_document(document_context)
    assert client.get(_url(document)).status_code == 200
    assert documents._COM_SLOT.acquire(timeout=1)
    try:
        started = time.monotonic()
        response = client.get(_url(document))
        assert response.status_code == 200 and response.content.startswith(b"%PDF")
        assert time.monotonic() - started < 2
    finally:
        documents._COM_SLOT.release()


def test_com_slot_timeout_returns_word_busy(document_context, fake_word, monkeypatch):  # noqa: F811
    client = document_context[0]
    document = create_document(document_context)
    monkeypatch.setattr(documents, "WORD_SLOT_TIMEOUT_SECONDS", 0.1)
    assert documents._COM_SLOT.acquire(timeout=1)
    try:
        response = client.get(_url(document), params={"scope": "edited", "expectedRevision": 0})
    finally:
        documents._COM_SLOT.release()
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "WORD_BUSY"
    assert response.headers["Retry-After"] == "5"
    assert fake_word[0] == []


def test_same_target_is_generated_once(document_context, fake_word):  # noqa: F811
    client = document_context[0]
    document = create_document(document_context)
    calls, fake = fake_word
    fake.delay = 0.4
    results = []
    barrier = threading.Barrier(3)

    def request():
        barrier.wait()
        results.append(client.get(_url(document), params={"scope": "edited", "expectedRevision": 0}).status_code)

    threads = [threading.Thread(target=request) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [200, 200, 200]
    assert len(calls) == 1
