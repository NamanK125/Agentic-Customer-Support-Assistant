from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


@pytest.fixture(scope="session")
def _exporter() -> InMemorySpanExporter:
    exp = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        from support_assistant.config import Settings
        from support_assistant.observability.otel import setup_telemetry

        setup_telemetry(
            Settings(otel_exporter_otlp_endpoint=None, otel_console=False), span_exporter=exp
        )
        if isinstance(trace.get_tracer_provider(), TracerProvider):
            return exp
        provider = trace.get_tracer_provider()
    provider.add_span_processor(SimpleSpanProcessor(exp))  # type: ignore[attr-defined]
    return exp


@pytest.fixture
def spans(_exporter: InMemorySpanExporter):
    _exporter.clear()

    def get(name: str) -> list[ReadableSpan]:
        return [s for s in _exporter.get_finished_spans() if s.name == name]

    return get
