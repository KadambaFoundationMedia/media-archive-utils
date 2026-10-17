"""File organizer helper: places files in processed/<category-folder>/."""

from mam.helpers.file_organizer.categories import Category, CategoryMatch, CategoryMatcher
from mam.helpers.file_organizer.organizer import (
    UNRESOLVED,
    FileOrganizer,
    OrganizeResult,
    assert_writable,
)

__all__ = [
    "UNRESOLVED",
    "Category",
    "CategoryMatch",
    "CategoryMatcher",
    "FileOrganizer",
    "OrganizeResult",
    "assert_writable",
]
