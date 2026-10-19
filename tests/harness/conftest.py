from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from support_assistant.observability import setup_telemetry
from support_assistant.tools import JsonOrderStore, OrderTools

from .helpers import fixed_clock


@pytest.fixture
def order_tools() -> OrderTools:
    return OrderTools(JsonOrderStore(clock=fixed_clock), clock=fixed_clock)


@pytest.fixture(scope="session")
def span_exporter() -> InMemorySpanExporter:
    setup_telemetry()  # no-op if another test package configured it first
    exporter = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


@pytest.fixture
def spans(span_exporter: InMemorySpanExporter) -> InMemorySpanExporter:
    span_exporter.clear()
    return span_exporter
