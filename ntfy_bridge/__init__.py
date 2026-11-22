"""ntfy bridge: run TradingAgents from a phone via ntfy.

This package is deliberately independent of the TradingAgents source tree. It
runs the project's CLI as a subprocess and talks only to:

1. the CLI's headless flags,
2. the on-disk state log / report tree the CLI writes,
3. the ``TRADINGAGENTS_*`` environment variables.

Nothing in ``tradingagents/`` or ``cli/`` is imported or modified, so upstream
merges never touch this directory and this directory never conflicts with one.
"""

__version__ = "1.0.0"
