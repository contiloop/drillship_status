"""Replaceable local OCR adapter for PDF pages without extractable text.

Deployment prerequisite: Tesseract 5 with English traineddata (for example the
Ubuntu package tesseract-ocr). No network/model call, source interpretation or
filesystem artifact is involved. Missing executables, OCR errors and results
without confidently recognized words fail rather than inventing page content.
"""
from __future__ import annotations

import csv
from io import BytesIO, StringIO
import shutil
import subprocess
from typing import Any, Protocol


class PageOCR(Protocol):
    def extract(self, page: Any, *, locator: str) -> str: ...


class TesseractPageOCR:
    def __init__(self, executable: str = "tesseract", *, resolution: int = 200, timeout: int = 45) -> None:
        self.executable = executable
        self.resolution = resolution
        self.timeout = timeout

    def extract(self, page: Any, *, locator: str) -> str:
        command = shutil.which(self.executable)
        if command is None:
            raise ValueError(f"OCR unavailable at {locator}: install Tesseract 5 with English traineddata")
        buffer = BytesIO()
        page.to_image(resolution=self.resolution).original.save(buffer, format="PNG")
        process = subprocess.run(
            [command, "stdin", "stdout", "-l", "eng", "--dpi", str(self.resolution), "--psm", "3", "tsv"],
            input=buffer.getvalue(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=self.timeout, check=False,
        )
        if process.returncode:
            raise ValueError(f"OCR failed at {locator} (exit {process.returncode})")
        rows = list(csv.DictReader(StringIO(process.stdout.decode("utf-8")), delimiter="\t", quoting=csv.QUOTE_NONE))
        words = [row for row in rows if row.get("level") == "5" and row.get("text", "").strip()]
        reliable = [row for row in words if float(row.get("conf") or -1) >= 60 and any(letter.isalpha() for letter in row["text"])]
        if not reliable:
            raise ValueError(f"OCR text unavailable at {locator}")
        # Keep every recognized word, including low-confidence ones. Coordinates
        # preserve the mechanical page layout; no word/contract filter is used.
        text_lines: dict[tuple[str, str, str], list[str]] = {}
        for row in words:
            key = (row["block_num"], row["par_num"], row["line_num"])
            text_lines.setdefault(key, []).append(row["text"])
        lines = ["OCR text (mechanical recognition; consult the original page image):"]
        lines.extend(" ".join(values) for values in text_lines.values())
        lines.append(f'OCR page pixels at {self.resolution} DPI; word boxes are x,y,width,height; confidence is 0..100.')
        for row in words:
            lines.append(f'[{row["left"]},{row["top"]},{row["width"]},{row["height"]}; confidence={row["conf"]}] {row["text"]}')
        return "\n".join(lines)
