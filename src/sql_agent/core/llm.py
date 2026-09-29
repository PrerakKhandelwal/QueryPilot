from __future__ import annotations

from abc import ABC, abstractmethod

from sql_agent.config import Settings
from sql_agent.core.errors import LLMError


class LLMProvider(ABC):
    @abstractmethod
    def complete(self, prompt: str, *, max_tokens: int, stop: list[str] | None = None) -> str:
        raise NotImplementedError


class OpenAIProvider(LLMProvider):
    def __init__(self, settings: Settings) -> None:
        from openai import OpenAI

        key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else None
        if not key:
            raise LLMError("OPENAI_API_KEY is required when LLM_PROVIDER=openai.")
        self._client = OpenAI(
            api_key=key,
            base_url=settings.openai_base_url or None,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )
        self._model = settings.openai_model

    def complete(self, prompt: str, *, max_tokens: int, stop: list[str] | None = None) -> str:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a careful data assistant. Follow instructions exactly.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_tokens=max_tokens,
                stop=stop,
            )
            text = (response.choices[0].message.content or "").strip()
            if not text:
                raise LLMError("LLM returned an empty response.")
            return text
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(f"OpenAI completion failed: {exc}") from exc


class AzureOpenAIProvider(LLMProvider):
    def __init__(self, settings: Settings) -> None:
        from openai import AzureOpenAI

        key = (
            settings.azure_openai_api_key.get_secret_value()
            if settings.azure_openai_api_key
            else None
        )
        if not key or not settings.azure_openai_endpoint:
            raise LLMError(
                "AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT are required when LLM_PROVIDER=azure."
            )
        self._client = AzureOpenAI(
            api_key=key,
            api_version=settings.azure_openai_api_version,
            azure_endpoint=settings.azure_openai_endpoint,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )
        self._model = settings.azure_openai_deployment

    def complete(self, prompt: str, *, max_tokens: int, stop: list[str] | None = None) -> str:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a careful data assistant. Follow instructions exactly.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_tokens=max_tokens,
                stop=stop,
            )
            text = (response.choices[0].message.content or "").strip()
            if not text:
                raise LLMError("LLM returned an empty response.")
            return text
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(f"Azure OpenAI completion failed: {exc}") from exc


class OllamaProvider(LLMProvider):
    """Local Ollama server using its OpenAI-compatible chat completions API."""

    def __init__(self, settings: Settings) -> None:
        from openai import OpenAI

        self._client = OpenAI(
            api_key="ollama",  # Ollama's local endpoint does not require authentication.
            base_url=settings.ollama_base_url.rstrip("/"),
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )
        self._model = settings.ollama_model

    def complete(self, prompt: str, *, max_tokens: int, stop: list[str] | None = None) -> str:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": "You are a careful data assistant. Follow instructions exactly."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_tokens=max_tokens,
                stop=stop,
            )
            text = (response.choices[0].message.content or "").strip()
            if not text:
                raise LLMError("Ollama returned an empty response.")
            return text
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(
                f"Could not reach Ollama at {settings.ollama_base_url} using model "
                f"'{settings.ollama_model}'. Start Ollama and pull the model first. Details: {exc}"
            ) from exc


class LocalHFProvider(LLMProvider):
    def __init__(self, settings: Settings, model_name: str | None = None) -> None:
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline
        except ImportError as exc:
            raise LLMError(
                "Local provider requires transformers, torch, and accelerate. "
                "Install requirements-local-llm.txt."
            ) from exc

        name = model_name or settings.local_text2sql_model_name
        self._tokenizer = AutoTokenizer.from_pretrained(name)
        kwargs: dict = {}
        if settings.local_device == "cuda":
            kwargs["device_map"] = "auto"
        self._model = AutoModelForCausalLM.from_pretrained(name, **kwargs)
        device = 0 if settings.local_device == "cuda" else -1
        self._pipe = pipeline(
            "text-generation",
            model=self._model,
            tokenizer=self._tokenizer,
            device=device if "device_map" not in kwargs else None,
        )

    def complete(self, prompt: str, *, max_tokens: int, stop: list[str] | None = None) -> str:
        try:
            outputs = self._pipe(
                prompt,
                max_new_tokens=max_tokens,
                do_sample=False,
                num_return_sequences=1,
                return_full_text=False,
            )
            text = (outputs[0].get("generated_text") or "").strip()
            if not text:
                raise LLMError("Local model returned empty text.")
            return text
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(f"Local generation failed: {exc}") from exc


def build_llm(settings: Settings, *, for_nl: bool = False) -> LLMProvider:
    if settings.llm_provider == "ollama":
        return OllamaProvider(settings)
    if settings.llm_provider == "openai":
        return OpenAIProvider(settings)
    if settings.llm_provider == "azure":
        return AzureOpenAIProvider(settings)
    model = settings.local_nl_model_name if for_nl and settings.local_nl_model_name else None
    return LocalHFProvider(settings, model_name=model)
