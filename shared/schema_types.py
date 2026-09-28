from typing import Annotated

from pydantic import StringConstraints

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

SlugStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=20, pattern=r'^[-a-zA-Z0-9_]+$'),
]

# Names stored in a CharField(max_length=200): reject longer input with a clear 422 instead of a database error.
NameStr200 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
