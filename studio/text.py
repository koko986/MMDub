import re
import unicodedata


MYANMAR_BASE = re.compile(r"[\u1000-\u1021]")
MYANMAR_ANY = re.compile(r"[\u1000-\u109f\uaa60-\uaa7f]")
BOUNDARY = re.compile(r"(?<=[။၊])|\n+")
SENTENCE_BOUNDARY = re.compile(r"(?<=[။!?])")
LONG_FORM_MAXIMUM = 50_000


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n+ *", "\n", text)
    return text.strip()


def syllable_count(text: str) -> int:
    return len(MYANMAR_BASE.findall(text or ""))


def validate_burmese(text: str, maximum: int = 500) -> tuple[str, int]:
    clean = normalize_text(text)
    if not clean:
        raise ValueError("Burmese text is required. / မြန်မာစာ ရိုက်ထည့်ပါ။")
    if not MYANMAR_ANY.search(clean):
        raise ValueError("Please enter Burmese text. / မြန်မာစာ ထည့်သွင်းပါ။")
    count = syllable_count(clean)
    if count > maximum:
        raise ValueError(
            f"Text is too long ({count}/{maximum} syllables). / စာသားရှည်လွန်းပါသည်။"
        )
    return clean, count


def validate_long_form(text: str) -> tuple[str, int]:
    return validate_burmese(text, maximum=LONG_FORM_MAXIMUM)


def split_paragraphs(text: str) -> list[str]:
    """Split long-form text without changing the text inside each paragraph."""
    clean = normalize_text(text)
    return [part.strip() for part in re.split(r"\n+", clean) if part.strip()]


def split_sentences(text: str) -> list[str]:
    clean = normalize_text(text)
    return [part.strip() for part in SENTENCE_BOUNDARY.split(clean) if part.strip()]


def _split_oversize(part: str, limit: int) -> list[str]:
    words = part.split(" ")
    result: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and syllable_count(candidate) > limit:
            result.append(current)
            current = word
        else:
            current = candidate
        while syllable_count(current) > limit:
            running = ""
            cut = 0
            for index, char in enumerate(current):
                running += char
                if syllable_count(running) >= limit:
                    cut = index + 1
                    break
            result.append(current[:cut].strip())
            current = current[cut:].strip()
    if current:
        result.append(current)
    return [item for item in result if item]


def split_passage(text: str, limit: int = 45) -> list[str]:
    clean, _ = validate_burmese(text, maximum=LONG_FORM_MAXIMUM)
    parts = [piece.strip() for piece in BOUNDARY.split(clean) if piece.strip()]
    chunks: list[str] = []
    current = ""
    for part in parts:
        for piece in _split_oversize(part, limit):
            candidate = f"{current} {piece}".strip()
            if current and syllable_count(candidate) > limit:
                chunks.append(current)
                current = piece
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks
