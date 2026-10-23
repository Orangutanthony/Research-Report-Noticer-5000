import re

from config import REGEX_RULES
from processors import Classification
from processors.text import posted_text

DESCRIPTION = "Regex Match"
ORDER = 5
USES_RULES = True


def rules():
    if not REGEX_RULES.is_file():
        raise RuntimeError("no regex rules uploaded, add them on the setup page")
    return [line.strip() for line in REGEX_RULES.read_text(encoding="utf-8").splitlines() if line.strip()]


def classify(doc):
    text = posted_text(doc)
    if text is None:
        return Classification(False, notes=(f"skipped, {doc.mimetype or 'unknown type'} is not a pdf or docx",))
    for pattern in rules():
        if re.search(pattern, text):
            return Classification(True, notes=(f"matched rule `{pattern}`",))
    return Classification(False, notes=("no rule matched",))
