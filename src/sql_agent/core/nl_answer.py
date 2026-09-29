from __future__ import annotations

from typing import Any, Sequence

from sql_agent.core.llm import LLMProvider


class NLAnswerGenerator:
    def __init__(self, llm: LLMProvider, max_tokens: int) -> None:
        self.llm = llm
        self.max_tokens = max_tokens

    def generate(
        self,
        question: str,
        sql: str,
        column_names: Sequence[str],
        rows: Sequence[Sequence[Any]],
    ) -> str:
        lines = [
            "You are an expert data analyst.",
            "Write a short, precise natural-language answer from the SQL result.",
            "Do not invent values. If the result is empty, say so.",
            "",
            f"Question: {question}",
            f"SQL: {sql}",
            "Columns: " + ", ".join(column_names),
            "Rows (at most 50):",
        ]
        for row in rows[:50]:
            lines.append(", ".join(str(v) for v in row))
        if len(rows) > 50:
            lines.append(f"... ({len(rows) - 50} more rows omitted)")
        if not rows:
            lines.append("(no rows)")
        lines.append("Answer:")
        raw = self.llm.complete("\n".join(lines), max_tokens=min(self.max_tokens, 400))
        lower = raw.lower()
        marker = "answer:"
        if marker in lower:
            return raw[lower.rfind(marker) + len(marker) :].strip()
        return raw.strip()
