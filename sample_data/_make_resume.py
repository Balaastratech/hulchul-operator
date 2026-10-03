"""Generate sample_data/resume.pdf for the fictional persona (stdlib only, deterministic).

Run:  python sample_data/_make_resume.py
Writes a one-page PDF with Helvetica text and prints its SHA-256.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

LINES: list[tuple[int, str]] = [
    (20, "Aarav Mehta"),
    (11, "Backend Engineer | Bengaluru, India"),
    (11, "aarav.mehta@example.test | +91 00000 00000 | https://example.test/aarav-mehta"),
    (0, ""),
    (14, "Summary"),
    (11, "Backend engineer with 6 years of experience building Python services, APIs and"),
    (11, "data pipelines. (Fictional persona for software testing. Not a real person.)"),
    (0, ""),
    (14, "Experience"),
    (11, "Senior Software Engineer, Northwind Example Labs (2022 - present)"),
    (11, "  - Led a team of 3 building order-tracking APIs in Python and PostgreSQL."),
    (11, "  - Cut p95 API latency by 40 percent through caching and query tuning."),
    (11, "Software Engineer, Contoso Example Systems (2019 - 2022)"),
    (11, "  - Built internal data pipelines and REST services for reporting."),
    (11, "  - Introduced automated testing that raised coverage from 35 to 80 percent."),
    (0, ""),
    (14, "Education"),
    (11, "B.Tech in Computer Science, Example Institute of Technology (2015 - 2019)"),
    (0, ""),
    (14, "Skills"),
    (11, "Python, SQL, PostgreSQL, Docker, REST APIs, Testing, AWS, Git"),
]


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf() -> bytes:
    content = ["BT"]
    y = 790
    for size, text in LINES:
        if size == 0:
            y -= 12
            continue
        content.append(f"/F1 {size} Tf 1 0 0 1 56 {y} Tm ({_esc(text)}) Tj")
        y -= size + 8
    content.append("ET")
    stream = "\n".join(content).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)


if __name__ == "__main__":
    data = build_pdf()
    target = Path(__file__).resolve().parent / "resume.pdf"
    target.write_bytes(data)
    print(f"{target} {len(data)} bytes sha256={hashlib.sha256(data).hexdigest()}")
