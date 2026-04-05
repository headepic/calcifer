"""Telemetry setup: initialize and shutdown OTel providers.

Gracefully degrades to noop when opentelemetry packages are not installed.
Users opt in by installing calcifer[telemetry].
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_initialized = False


def _has_otel() -> bool:
    try:
        import opentelemetry  # noqa: F401
        return True
    except ImportError:
        return False


def init_telemetry(
    service_name: str = "calcifer",
    *,
    otlp_endpoint: str | None = None,
    otlp_protocol: str = "grpc",  # "grpc" | "http/protobuf"
    export_interval_ms: int = 5000,
    console_export: bool = False,
) -> bool:
    """Initialize OpenTelemetry tracing + metrics.

    Returns True if OTel is available and initialized.
    Returns False (noop) if opentelemetry is not installed.
    """
    global _initialized
    if _initialized:
        return True

    if not _has_otel():
        logger.debug("opentelemetry not installed, telemetry disabled")
        return False

    try:
        from opentelemetry import trace, metrics
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
        )
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import (
            PeriodicExportingMetricReader,
            ConsoleMetricExporter,
        )
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create({"service.name": service_name})

        # -- Traces --
        tracer_provider = TracerProvider(resource=resource)

        if otlp_endpoint:
            if otlp_protocol == "grpc":
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            else:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            tracer_provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint))
            )

        if console_export:
            tracer_provider.add_span_processor(
                BatchSpanProcessor(ConsoleSpanExporter())
            )

        trace.set_tracer_provider(tracer_provider)

        # -- Metrics --
        readers = []

        if otlp_endpoint:
            if otlp_protocol == "grpc":
                from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
            else:
                from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
            readers.append(
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(endpoint=otlp_endpoint),
                    export_interval_millis=export_interval_ms,
                )
            )

        if console_export:
            readers.append(
                PeriodicExportingMetricReader(
                    ConsoleMetricExporter(),
                    export_interval_millis=export_interval_ms,
                )
            )

        if readers:
            meter_provider = MeterProvider(resource=resource, metric_readers=readers)
            metrics.set_meter_provider(meter_provider)

        _initialized = True
        logger.info("Telemetry initialized (endpoint=%s)", otlp_endpoint)
        return True

    except Exception as e:
        logger.warning("Failed to initialize telemetry: %s", e)
        return False


async def shutdown_telemetry(timeout_ms: int = 5000) -> None:
    """Flush and shutdown telemetry providers."""
    global _initialized
    if not _initialized or not _has_otel():
        return

    try:
        from opentelemetry import trace, metrics

        tracer_provider = trace.get_tracer_provider()
        if hasattr(tracer_provider, "shutdown"):
            tracer_provider.shutdown()

        meter_provider = metrics.get_meter_provider()
        if hasattr(meter_provider, "shutdown"):
            meter_provider.shutdown()

        _initialized = False
        logger.debug("Telemetry shutdown complete")
    except Exception as e:
        logger.warning("Telemetry shutdown error: %s", e)
