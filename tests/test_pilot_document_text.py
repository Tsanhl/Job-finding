import asyncio
from zipfile import ZipFile

import pytest

from src.pilot.document_text import parse, preview
from src.pilot.resources import Cache, Documents
from src.pilot.store import Store


def test_docx_text_is_unconfirmed_and_preview_cache_tracks_hash(tmp_path):
    document = tmp_path / "sample.docx"
    with ZipFile(document, "w") as z:
        z.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>Synthetic candidate</w:t></w:r></w:p></w:document>',
        )
    result = parse(document)
    assert result["text"] == "Synthetic candidate" and result["confirmed"] is False

    async def scenario():
        s = Store(tmp_path / "db")
        cache = Cache(s)
        id = Documents(s).register(document, "cv", approved=True)
        assert await preview(s, cache, id) == result
        assert await preview(s, cache, id) == result
        assert cache.hits == 1
        document.write_bytes(b"changed")
        with pytest.raises(ValueError, match="changed"):
            await preview(s, cache, id)
        cache.clear()
        assert s.one("SELECT id FROM documents")["id"] == id
        s.close()

    asyncio.run(scenario())
