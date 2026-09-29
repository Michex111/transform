"""Escaping for LIKE/ILIKE patterns built from untrusted input.

WHY this lives in one module instead of being inlined in each repository: the
escaping *is* the correctness boundary of a name search. A model-supplied term
reaches SQL as a pattern, so an unescaped ``%`` silently turns "50% report" into
"match anything" and an unescaped ``_`` makes "a_b" match "axb" — the user asked
for a literal string and got a wider result set without being told. One
implementation means a fix cannot land in one repository and be forgotten in the
other.
"""


def escape_like(value: str) -> str:
    """Escape ``\\``, ``%`` and ``_`` so ``value`` matches literally in a LIKE.

    The backslash is replaced *first*: it is the escape character itself, so
    escaping it after the others would double the backslashes this function just
    inserted. Callers must pair the result with ``escape="\\\\"`` on the
    ``ilike``/``like`` call, which is what tells the backend which character is
    doing the escaping.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
