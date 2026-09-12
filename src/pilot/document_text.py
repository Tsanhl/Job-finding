"""Local approved-document text previews; extracted text never confirms facts."""

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

MAX_TEXT = 100_000


def parse(path):
    path = Path(path)
    if path.stat().st_size > 25 * 1024 * 1024:
        raise ValueError("Document exceeds extraction size limit")
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(path)
        if reader.is_encrypted or len(reader.pages) > 100:
            raise ValueError("Encrypted document or page limit exceeded")
        chunks = []
        for page in reader.pages:
            chunks.append((page.extract_text() or "")[:MAX_TEXT])
            if sum(map(len, chunks)) >= MAX_TEXT:
                break
        text = "\n".join(chunks)
    elif path.suffix.lower() == ".docx":
        with ZipFile(path) as archive:
            info = archive.getinfo("word/document.xml")
            if (
                info.file_size > 2 * 1024 * 1024
                or info.file_size / max(1, info.compress_size) > 200
            ):
                raise ValueError("DOCX extraction size limit exceeded")
            tree = ElementTree.fromstring(archive.read(info))
            text = "\n".join(
                "".join(e.itertext())
                for e in tree.iter(
                    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
                )
            )
    elif path.suffix.lower() == ".txt":
        text = path.read_text(errors="replace")
    else:
        raise ValueError(
            "Text preview supports PDF, DOCX and TXT; upload support is separate"
        )
    return {
        "text": text[:MAX_TEXT],
        "truncated": len(text) > MAX_TEXT,
        "confirmed": False,
    }


async def preview(store, cache, document_id):
    document = store.one(
        "SELECT * FROM documents WHERE id=? AND approved=1", (document_id,)
    )
    if not document:
        raise ValueError("Select an approved document")
    path = Path(document["path"])
    if hashlib.sha256(path.read_bytes()).hexdigest() != document["sha256"]:
        raise ValueError("Document changed; register its new version")

    async def extract():
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "src.pilot.document_text",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=20)
        except BaseException:
            process.kill()
            await process.communicate()
            raise
        if process.returncode:
            raise ValueError(
                "Document text could not be extracted within the supported limits"
            )
        return json.loads(output)

    return await cache.derive(
        "extraction:" + document["sha256"] + ":text-v1", "extraction", extract
    )


if __name__ == "__main__":
    try:
        print(json.dumps(parse(sys.argv[1])))
    except Exception:
        raise SystemExit(1)
