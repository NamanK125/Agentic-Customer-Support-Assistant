"""Runtime configuration, loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ModelRole = Literal["router", "guard", "agent"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM: any OpenAI-compatible endpoint. Default is Groq; a self-hosted vLLM server works by
    # changing LLM_BASE_URL / LLM_MODEL (see .env.example).
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_api_key: str = ""
    llm_api_keys_extra: str = ""
    llm_model: str = "llama-3.3-70b-versatile"
    # Routing is a short classification: the 8B model is faster, cheaper and has higher limits
    llm_model_router: str | None = "llama-3.1-8b-instant"
    llm_model_guard: str | None = None
    llm_model_agent: str | None = None
    # Outputs are always parsed tolerantly and validated with pydantic, whichever mode is used
    llm_structured_output: Literal["json_schema", "json_object", "none"] = "json_object"
    # vLLM + Qwen3-style thinking models: send chat_template_kwargs.enable_thinking=false. Without
    # it the model writes its reasoning into `content` and is ~8x slower per call. Off for Groq.
    llm_disable_thinking: bool = False
    llm_timeout_s: float = 20.0
    llm_max_retries: int = 2
    # Max in-flight LLM calls per role per process (backpressure toward the model server)
    llm_max_concurrency: int = 32
    # Circuit breaker: open after N consecutive failed calls, retry one probe after the cooldown
    llm_circuit_failures: int = 3
    llm_circuit_cooldown_s: float = 15.0

    # Harness
    turn_timeout_s: float = 30.0
    router_confidence_threshold: float = 0.5
    orders_path: str | None = None  # None = <repo>/data/orders.json

    # Retrieval
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    rag_min_score: float | None = None  # None = per-model calibrated default (rag/retriever.py)
    kb_dir: str | None = None  # None = <repo>/kb
    rag_cache_dir: str | None = None  # None = <repo>/.cache/rag

    # Shared state for horizontal scale-out; None = in-process memory
    redis_url: str | None = None
    cache_ttl_s: int = 3600

    # Observability
    otel_service_name: str = "support-assistant"
    otel_exporter_otlp_endpoint: str | None = None
    otel_console: bool = False

    def model_for(self, role: ModelRole) -> str:
        override = {
            "router": self.llm_model_router,
            "guard": self.llm_model_guard,
            "agent": self.llm_model_agent,
        }[role]
        return override or self.llm_model

    def api_keys(self) -> list[str]:
        extra = [k.strip() for k in self.llm_api_keys_extra.split(",") if k.strip()]
        return [self.llm_api_key, *extra]


@lru_cache
def get_settings() -> Settings:
    return Settings()
