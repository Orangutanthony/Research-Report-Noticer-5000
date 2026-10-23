from processors import llm

DESCRIPTION = "LLM"
ORDER = 1
PROVIDERS = {"claude": "Claude Code CLI", "codex": "Codex CLI", "antigravity": "Gemini (Antigravity CLI)", "local": "Local LLM"}
DESCRIBES = "summary"


def summarize(doc):
    return llm.write_summary(doc, llm.asker("SUMMARY"))
