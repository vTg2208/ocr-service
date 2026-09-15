"""Conservative FRA document-type detection from OCR page text."""

import re


DOCUMENT_PATTERNS = {
    "claim_form": (
        re.compile(r"\b(?:fra|forest rights(?: act)?)\s+(?:claim|application)\s+form\b", re.I),
        re.compile(r"\bclaim\s+form\s+for\b[^\n]{0,80}\bforest\s+righ\w+\b", re.I),
        re.compile(r"\bclaim(?:ant)?\s*(?:name)?\s*[:：-].*\b(?:right|claim)\s*type\b", re.I | re.S),
    ),
    "gram_sabha_record": (re.compile(r"\bgram\s+sabha\s+(?:resolution|proceedings|minutes|record)\b", re.I),),
    "sdlc_record": (re.compile(r"\bSDLC\s+(?:decision|proceedings|record|resolution)\b", re.I),),
    "dlc_record": (re.compile(r"\bDLC\s+(?:decision|proceedings|record|resolution)\b", re.I),),
    "title_right_record": (
        re.compile(r"\b(?:record\s+of\s+forest\s+rights|forest\s+right\s+title|RoFR\s+(?:title|certificate))\b", re.I),
    ),
    "map_sketch": (re.compile(r"\b(?:claim|forest\s+right)\s+(?:map|sketch)\b", re.I),),
}


def classify_fra_document(pages: list[dict]) -> dict:
    """Return a type only when one category has an OCR-text match."""

    matches = []
    for page in pages:
        text = str(page.get("text") or "")
        for document_type, patterns in DOCUMENT_PATTERNS.items():
            for pattern in patterns:
                match = pattern.search(text)
                if match:
                    matches.append({
                        "document_type": document_type,
                        "source_page": page.get("page_number"),
                    })
                    break
    types = {item["document_type"] for item in matches}
    if len(types) == 1:
        selected = matches[0]
        return {**selected, "method": "fra_ocr_keyword_v1", "ambiguous": False}
    return {
        "document_type": "unknown",
        "source_page": None,
        "method": "fra_ocr_keyword_v1",
        "ambiguous": len(types) > 1,
        "candidate_types": sorted(types),
    }
