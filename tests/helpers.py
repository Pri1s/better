from __future__ import annotations


def rows(con, sql: str, *params) -> list[tuple]:
    return con.execute(sql, list(params)).fetchall()


def one(con, sql: str, *params) -> dict:
    """The single row a query returns, as a dict."""
    cursor = con.execute(sql, list(params))
    names = [column[0] for column in cursor.description]
    result = cursor.fetchall()
    assert len(result) == 1, f"expected one row, got {len(result)}"
    return dict(zip(names, result[0], strict=True))


def scalar(con, sql: str, *params):
    return con.execute(sql, list(params)).fetchone()[0]
