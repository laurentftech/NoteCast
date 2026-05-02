"""Domain exceptions for the NoteCast application."""


class NotebookLMError(Exception):
    """Base exception for NotebookLM-related errors."""


class AuthError(Exception):
    """Authentication or authorization error."""


class FeedError(Exception):
    """Feed generation or parsing error."""


class ConfigError(Exception):
    """Configuration validation or loading error."""
