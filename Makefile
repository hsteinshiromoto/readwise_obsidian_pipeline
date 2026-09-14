.PHONY: test sync

test:
	uv run python -m unittest discover -s tests -t . -v

sync:
	uv run python -m reader_sync sync
