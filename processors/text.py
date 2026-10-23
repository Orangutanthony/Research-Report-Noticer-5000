import io

import docx
import pymupdf

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PLAIN = "text/plain"
SUFFIXES = {".pdf": PDF, ".docx": DOCX, ".txt": PLAIN}


def extract(data, mimetype):
    if mimetype == PDF:
        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            return "\n".join(page.get_text() for page in pdf)
    if mimetype == DOCX:
        return "\n".join(p.text for p in docx.Document(io.BytesIO(data)).paragraphs)
    if mimetype == PLAIN:
        return data.decode("utf-8", errors="replace")
    return None


def posted_text(doc):
    # only pdf and docx posts are read, text snippets and images never are
    if doc.mimetype not in (PDF, DOCX):
        return None
    return extract(doc.read(), doc.mimetype)


def file_text(path):
    mimetype = SUFFIXES.get(path.suffix.lower())
    return extract(path.read_bytes(), mimetype) if mimetype else None
