"""Application Insights via the Azure Monitor OpenTelemetry distro.

Does nothing unless APPLICATIONINSIGHTS_CONNECTION_STRING is set, so tests and local runs stay
quiet. In Azure the connection string is a Key Vault secret surfaced as an env var.
"""

from __future__ import annotations

import logging
import os

_enabled = False


def setup_telemetry(service_name: str) -> bool:
    global _enabled
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    if _enabled:
        return True
    if not os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        return False
    os.environ.setdefault("OTEL_SERVICE_NAME", service_name)  # shows as cloud_RoleName
    from azure.monitor.opentelemetry import configure_azure_monitor

    # Only export our own "rul.*" loggers, not every library's chatter.
    configure_azure_monitor(logger_name="rul")
    _enabled = True
    return True


def is_enabled() -> bool:
    return _enabled


def flush() -> None:
    """Push buffered telemetry before a short-lived process (like the drift job) exits."""
    if not _enabled:
        return
    from opentelemetry import _logs, metrics, trace

    for provider in (
        trace.get_tracer_provider(),
        metrics.get_meter_provider(),
        _logs.get_logger_provider(),
    ):
        force_flush = getattr(provider, "force_flush", None)
        if force_flush:
            force_flush()
