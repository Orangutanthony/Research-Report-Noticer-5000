from processors import llm

DESCRIPTION = "LLM"
ORDER = 1
USES_EXAMPLES = True
PROVIDERS = {"claude": "Claude Code CLI", "codex": "Codex CLI", "antigravity": "Gemini (Antigravity CLI)", "local": "Local LLM"}
DESCRIBES = "report"


def classify(doc):
    return llm.decide(doc, llm.asker("LLM"))
