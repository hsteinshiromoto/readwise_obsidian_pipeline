"""Collect the docstring examples of every module into one test suite."""

import doctest

from reader_sync import api, cli, pipeline, state, writers

MODULES = (api, cli, pipeline, state, writers)


def load_tests(loader, tests, ignore):
    """Add the doctests of each module to the unittest suite.

    Args:
        loader: The active unittest loader.
        tests: The suite collected so far.
        ignore: Unused, required by the unittest protocol.

    Returns:
        The suite with every module's doctests added.

    Example:
        Run the suite from the repository root::

            uv run python -m unittest discover -s tests -t .
    """
    for module in MODULES:
        tests.addTests(doctest.DocTestSuite(module))
    return tests
