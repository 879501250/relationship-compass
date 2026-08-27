"""Module entrypoint for ``python -m eval_console``."""

from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())
