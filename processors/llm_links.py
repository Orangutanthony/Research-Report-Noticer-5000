from processors import llm

DESCRIPTION = "LLM"
ORDER = 1
PROVIDERS = {"claude": "Claude Code CLI", "codex": "Codex CLI", "antigravity": "Gemini (Antigravity CLI)", "local": "Local LLM"}
DESCRIBES = "links"
USES_GITHUB = True


def find_links(doc, summary, week):
    return llm.pick_links(doc, summary, week, llm.asker("LINKS"))
