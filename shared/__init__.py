"""Shared utilities for the self-contained `causal_signal_research` experiment tree.

This package holds reusable helpers that every baseline or future experiment in this
research stream can import without reaching outside the `causal_signal_research`
folder.
"""

from shared.temporal_sources import (
    FlowFeaturesRequest,
    FlowFeaturesSnapshot,
    FlowWindowRequest,
    FlowWindowSnapshot,
    FlowWindowSummary,
    FredLatestRequest,
    FredObservation,
    OptionsWindowRequest,
    PriceScheduleRequest,
    SourceSnapshot,
    TemporalSourceRegistry,
    UniverseMembershipRequest,
    build_default_registry,
)

__all__ = [
    "FredLatestRequest",
    "FredObservation",
    "FlowFeaturesRequest",
    "FlowFeaturesSnapshot",
    "FlowWindowRequest",
    "FlowWindowSnapshot",
    "FlowWindowSummary",
    "OptionsWindowRequest",
    "PriceScheduleRequest",
    "SourceSnapshot",
    "TemporalSourceRegistry",
    "UniverseMembershipRequest",
    "build_default_registry",
]
