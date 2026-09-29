"""Compatibility entry point; new callers should use world_generation.main."""
from .main import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
