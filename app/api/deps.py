"""Access to the per-application service objects created in `app.main.create_app`."""

from fastapi import Request

from app.services.simulation import Services


def get_services(request: Request) -> Services:
    return request.app.state.services
