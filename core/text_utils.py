"""
text_utils.py — Text splitting, SRT parsing, and text normalization utilities.
"""

import re
import os
from typing import List, Tuple


def split_text(text: str, mode: str = "paragraph", max_chars: int = 5000) -> List[str]:
    """Split text into chunks for TTS processing.

    Args:
        text: Input text to split.
        mode: Split strategy — 'paragraph', 'sentence', or 'fixed'.
        max_chars: Maximum characters per chunk.

    Returns:
        List of text chunks.
    """
    text = text.strip()
    if not text:
        return []

    if mode == "paragraph":
        return _split_by_paragraph(text, max_chars)
    elif mode == "sentence":
        return _split_by_sentence(text, max_chars)
    else:
        return _split_fixed(text, max_chars)


def _split_by_paragraph(text: str, max_chars: int) -> List[str]:
    """Split by double newlines (paragraphs), merging small ones."""
    raw_paragraphs = re.split(r'\n\s*\n', text)
    chunks: List[str] = []
    current = ""

    for para in raw_paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) + 2 <= max_chars:
            current = f"{current}\n\n{para}" if current else para
        else:
            if current:
                chunks.append(current)
            # If single paragraph exceeds max_chars, split by sentence
            if len(para) > max_chars:
                chunks.extend(_split_by_sentence(para, max_chars))
                current = ""
            else:
                current = para

    if current:
        chunks.append(current)
    return chunks


def _split_by_sentence(text: str, max_chars: int) -> List[str]:
    """Split by sentence boundaries, respecting max_chars."""
    # Vietnamese and English sentence boundaries
    sentences = re.split(r'(?<=[.!?。！？])\s+', text)
    chunks: List[str] = []
    current = ""

    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        if len(current) + len(sent) + 1 <= max_chars:
            current = f"{current} {sent}" if current else sent
        else:
            if current:
                chunks.append(current)
            if len(sent) > max_chars:
                chunks.extend(_split_fixed(sent, max_chars))
                current = ""
            else:
                current = sent

    if current:
        chunks.append(current)
    return chunks


def _split_fixed(text: str, max_chars: int) -> List[str]:
    """Split at fixed character intervals, trying to break at word boundaries."""
    chunks: List[str] = []
    while len(text) > max_chars:
        # Find last space within max_chars
        idx = text.rfind(' ', 0, max_chars)
        if idx == -1:
            idx = max_chars
        chunks.append(text[:idx].strip())
        text = text[idx:].strip()
    if text:
        chunks.append(text)
    return chunks


def count_chars(text: str) -> int:
    """Count characters excluding whitespace-only content."""
    return len(text.strip())


def clean_text(text: str) -> str:
    """Normalize text for TTS: collapse whitespace, fix quotes, etc."""
    # Collapse multiple spaces
    text = re.sub(r'[ \t]+', ' ', text)
    # Collapse multiple newlines to double
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Fix smart quotes
    text = text.replace('\u201c', '"').replace('\u201d', '"')
    text = text.replace('\u2018', "'").replace('\u2019', "'")
    # Strip leading/trailing
    return text.strip()


# ─── SRT Parser ──────────────────────────────────────────────────

class SRTEntry:
    """A single SRT subtitle entry."""
    __slots__ = ("index", "start_time", "end_time", "text")

    def __init__(self, index: int, start_time: str, end_time: str, text: str):
        self.index = index
        self.start_time = start_time
        self.end_time = end_time
        self.text = text

    def to_dict(self):
        return {
            "index": self.index,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "text": self.text,
        }

    def start_seconds(self) -> float:
        return _srt_time_to_seconds(self.start_time)

    def end_seconds(self) -> float:
        return _srt_time_to_seconds(self.end_time)


def _srt_time_to_seconds(ts: str) -> float:
    """Convert '00:01:23,456' → 83.456"""
    ts = ts.replace(',', '.')
    parts = ts.split(':')
    h, m, s = float(parts[0]), float(parts[1]), float(parts[2])
    return h * 3600 + m * 60 + s


def parse_srt(content: str) -> List[SRTEntry]:
    """Parse SRT subtitle content into a list of SRTEntry objects."""
    entries: List[SRTEntry] = []
    blocks = re.split(r'\n\s*\n', content.strip())

    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 3:
            continue
        try:
            index = int(lines[0].strip())
        except ValueError:
            continue

        time_match = re.match(
            r'(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})',
            lines[1].strip()
        )
        if not time_match:
            continue

        text = '\n'.join(lines[2:]).strip()
        # Remove HTML tags from SRT
        text = re.sub(r'<[^>]+>', '', text)

        entries.append(SRTEntry(
            index=index,
            start_time=time_match.group(1),
            end_time=time_match.group(2),
            text=text,
        ))

    return entries


def read_text_file(filepath: str) -> str:
    """Read a text file, auto-detecting encoding."""
    encodings = ['utf-8', 'utf-8-sig', 'utf-16', 'cp1252', 'latin-1']
    for enc in encodings:
        try:
            with open(filepath, 'r', encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    # Last resort: read as binary and decode with errors='replace'
    with open(filepath, 'rb') as f:
        return f.read().decode('utf-8', errors='replace')


def get_file_type(filepath: str) -> str:
    """Determine file type from extension."""
    ext = os.path.splitext(filepath)[1].lower()
    if ext == '.srt':
        return 'srt'
    elif ext in ('.txt', '.text'):
        return 'text'
    elif ext in ('.md', '.markdown'):
        return 'markdown'
    elif ext in ('.docx',):
        return 'docx'
    return 'text'
