from __future__ import annotations

from sql_agent.core.cache import CachedExample
from sql_agent.core.llm import LLMProvider
from sql_agent.core.rate_limit import extract_sql_text
from sql_agent.core.schema import SchemaSnapshot


class Text2SQLGenerator:
    def __init__(self, llm: LLMProvider, dialect: str, max_tokens: int) -> None:
        self.llm = llm
        self.dialect = dialect
        self.max_tokens = max_tokens

    def build_prompt(
        self,
        question: str,
        schema: SchemaSnapshot,
        few_shot_examples: list[CachedExample],
        schema_tables: list[str] | None = None,
        repair_hint: str | None = None,
    ) -> str:
        lines = [
            "You are an expert analyst who writes a single read-only SQL query.",
            f"Target SQL dialect: {self.dialect}.",
            "Rules:",
            "- Return ONLY one SQL query. No markdown, no explanation.",
            "- SELECT / WITH (select) / UNION only. Never INSERT, UPDATE, DELETE, DDL, or multiple statements.",
            "- Use only tables and columns listed in the schema.",
            "- Prefer explicit column names over SELECT *.",
            "- Do not use SELECT INTO, FOR UPDATE, or administrative functions.",
            "",
            schema.to_prompt_string(include_tables=schema_tables),
            "",
        ]
        if few_shot_examples:
            lines.append("Previous correct examples:")
            for ex in few_shot_examples:
                lines.append(f"Q: {ex.question}")
                lines.append(f"SQL: {ex.sql}")
                lines.append("")
        lines.append(f"Question: {question}")
        if repair_hint:
            lines.append("The previous SQL was rejected or failed. Fix it.")
            lines.append(f"Error: {repair_hint}")
        lines.append("SQL:")
        return "\n".join(lines)

    def generate(
        self,
        question: str,
        schema: SchemaSnapshot,
        few_shot_examples: list[CachedExample] | None = None,
        schema_tables: list[str] | None = None,
        repair_hint: str | None = None,
    ) -> str:
        prompt = self.build_prompt(
            question,
            schema,
            few_shot_examples or [],
            schema_tables=schema_tables,
            repair_hint=repair_hint,
        )
        raw = self.llm.complete(prompt, max_tokens=self.max_tokens)
        return extract_sql_text(raw)
