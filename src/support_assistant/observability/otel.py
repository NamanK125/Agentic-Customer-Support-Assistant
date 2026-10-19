"""OpenTelemetry setup: traces + metrics, exported over OTLP when configured.

Components call `tracer(__name__)` / `meter(__name__)` and never touch providers directly.
Without an OTLP endpoint (and OTEL_CONSOLE unset) spans are still created, so trace ids and
in-memory test exporters work, but nothing is shipped anywhere.
"""

from __future__ import annotations

import threading

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    MetricReader,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)

from support_assistant.config import Settings, get_settings

_lock = threading.Lock()
_configured = False


def setup_telemetry(
    settings: Settings | None = None,
    *,
    span_exporter: SpanExporter | None = None,
    metric_reader: MetricReader | None = None,
) -> None:
    """Install global tracer/meter providers once. Extra exporters/readers are for tests."""
    global _configured
    with _lock:
        if _configured:
            return
        s = settings or get_settings()
        resource = Resource.create({"service.name": s.otel_service_name})

        tp = TracerProvider(resource=resource)
        readers: list[MetricReader] = []
        if s.otel_exporter_otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

            tp.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=s.otel_exporter_otlp_endpoint))
            )
            readers.append(
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(endpoint=s.otel_exporter_otlp_endpoint)
                )
            )
        if s.otel_console:
            tp.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
            readers.append(PeriodicExportingMetricReader(ConsoleMetricExporter()))
        if span_exporter is not None:
            tp.add_span_processor(SimpleSpanProcessor(span_exporter))
        if metric_reader is not None:
            readers.append(metric_reader)

        trace.set_tracer_provider(tp)
        metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=readers))
        _configured = True


def tracer(name: str) -> trace.Tracer:
    return trace.get_tracer(name)


def meter(name: str) -> metrics.Meter:
    return metrics.get_meter(name)
