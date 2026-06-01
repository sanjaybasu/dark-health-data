.PHONY: help install install-all demo test lint clean

help:
	@echo "Dark Health Data — make targets"
	@echo "  install      install core (offline demo works with this)"
	@echo "  install-all  install with pdf + llm + parquet extras"
	@echo "  demo         run the EQR pipeline offline on synthetic fixtures"
	@echo "  test         run the test suite"
	@echo "  lint         ruff check"
	@echo "  clean        remove build + generated data outputs"

install:
	python -m pip install -e .

install-all:
	python -m pip install -e ".[all,dev]"

demo:
	python scripts/run_demo.py

test:
	python -m pytest -q

lint:
	ruff check src tests

clean:
	rm -rf build dist *.egg-info src/*.egg-info .pytest_cache
	rm -rf data/raw data/cache data/processed
