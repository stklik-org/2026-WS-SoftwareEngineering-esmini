"""Helpers shared by unit tests."""


def scenario_with(directive: str) -> bytes:
    """A minimal scenario whose XML comment carries a directive for the fake esmini (see conftest.py)."""
    return f'<?xml version="1.0"?>\n<!-- {directive} -->\n<OpenSCENARIO><FileHeader/></OpenSCENARIO>\n'.encode()
