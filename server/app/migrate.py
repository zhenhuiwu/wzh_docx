"""Run the SQLite migrations from the command line."""

from app.db import migrate


def run_migrations() -> None:
    for name in migrate():
        print(f"Applied: {name}")
    from app.migrate_template_versions import backfill

    report = backfill()
    if report["templateDocuments"] or report["bound"]:
        print(f"Template versions backfill: {report}")
    print("SQLite migrations complete")


if __name__ == "__main__":
    run_migrations()
