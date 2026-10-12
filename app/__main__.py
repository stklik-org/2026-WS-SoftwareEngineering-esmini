"""Entry point: `python -m app` starts uvicorn with the settings taken from the environment."""

import uvicorn

from app.core.settings import Settings


def main() -> None:
    settings = Settings.from_env()
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        server_header=False,
        # Never enable reload/debug here: the container must not run a development server (PLAN.md §6.7).
    )


if __name__ == "__main__":
    main()
