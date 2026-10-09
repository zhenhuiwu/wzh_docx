"""Run the SQLite migrations from the command line."""

from app.db import migrate


def run_migrations() -> None:
    for name in migrate():
        print(f"Applied: {name}")
    print("SQLite migrations complete")


if __name__ == "__main__":
    run_migrations()
