"""CLI command to approve a pending screenshot request."""

from app.server.vision import approve


def main() -> None:
    approve()
    print("Screenshot approved.")


if __name__ == "__main__":
    main()
