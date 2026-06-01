from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parents[3]
WAREHOUSE = ROOT / "demo" / "duckdb" / "out" / "warehouse.duckdb"


def print_table(headers: list[str], rows: list[tuple[object, ...]]) -> None:
    widths = [
        max(len(header), *(len(str(row[index])) for row in rows))
        for index, header in enumerate(headers)
    ]
    print("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(str(value).ljust(widths[index]) for index, value in enumerate(row)))


def main() -> None:
    if not WAREHOUSE.exists():
        raise SystemExit(f"Warehouse does not exist yet: {WAREHOUSE}")

    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    print("\nOrders by file hash:")
    print_table(
        ["file_hash", "rows", "total_amount"],
        con.sql(
            """
            select
              left(_source_file_hash, 12) as file_hash,
              count(*) as rows,
              round(sum(amount), 2) as total_amount
            from orders
            group by 1
            order by 1
            """
        ).fetchall(),
    )

    print("\nLoaded rows:")
    print_table(
        ["order_id", "customer_id", "order_date", "amount", "status", "file_hash"],
        con.sql(
            """
            select
              order_id,
              customer_id,
              order_date,
              amount,
              status,
              left(_source_file_hash, 12) as file_hash
            from orders
            order by order_id
            """
        ).fetchall(),
    )


if __name__ == "__main__":
    main()
