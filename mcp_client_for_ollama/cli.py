#!/usr/bin/env python
"""Command-line interface for the MCP Client for Ollama."""

import sys

from .client import app, RESUME_LAST


def _normalize_resume_args(argv):
    """Let bare --resume/-r (no value) mean 'last session in the dir'.

    Typer/Click can't express an optional-value option, so we rewrite a value-less
    --resume into `--resume __LAST__` before Typer parses argv.
    """
    out = []
    for i, tok in enumerate(argv):
        out.append(tok)
        if tok in ("--resume", "-r"):
            nxt = argv[i + 1] if i + 1 < len(argv) else None
            if nxt is None or nxt.startswith("-"):
                out.append(RESUME_LAST)
    return out


def run_cli():
    """Run the MCP Client for Ollama command-line interface."""
    sys.argv[1:] = _normalize_resume_args(sys.argv[1:])
    app()

if __name__ == "__main__":
    run_cli()
