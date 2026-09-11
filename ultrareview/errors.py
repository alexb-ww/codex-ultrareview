"""Error types raised by the ultrareview package."""
from __future__ import annotations


class UltraReviewError(Exception):
    """Base class for every error this package raises deliberately."""


class ConfigError(UltraReviewError):
    """Invalid command-line or configuration input."""


class GitError(UltraReviewError):
    """A git command failed or git is unavailable."""


class ScopeError(UltraReviewError):
    """The requested review scope cannot be resolved."""


class LimitExceeded(UltraReviewError):
    """The selected diff is larger than the configured limits."""


class RunnerError(UltraReviewError):
    """The codex process could not be started or produced no usable output."""


class GateError(UltraReviewError):
    """The report failed a structural check that cannot be downgraded."""
