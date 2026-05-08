"""Packaging and bootstrap coverage for the API package."""

from __future__ import annotations


def test_defi_sim_api_package_is_importable():
    import defi_sim_api
    from defi_sim_api.main import app

    assert defi_sim_api.__doc__
    assert app.title == "defi-sim API"
