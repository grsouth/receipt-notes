from waitress import serve

from app import app, server_host, server_port, start_scheduler


def main() -> None:
    """Run the scheduler and HTTP server in one long-lived process."""
    start_scheduler()
    serve(
        app,
        host=server_host(),
        port=server_port(),
        threads=4,
    )


if __name__ == "__main__":
    main()
