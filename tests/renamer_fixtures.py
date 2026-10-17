"""A small, fixed reference set so Renamer tests never call Baserow."""

import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def reference_raw() -> dict:
    categories = json.loads((FIXTURES / "category_terms.json").read_text(encoding="utf-8"))
    return {
        "categories": categories,
        "travel": [
            {
                "start_date": "2011-08-26",
                "end_date": "2011-08-30",
                "place_location": "Oslo",
                "country": "Norway",
            },
            {
                "start_date": "2011-09-01",
                "end_date": "2011-09-08",
                "place_location": "Berlin",
                "country": "Germany",
            },
            {
                "start_date": "2012-01-15",
                "end_date": "2012-01-23",
                "place_location": "Radhadesh",
                "country": "Belgium",
            },
            {
                "start_date": "2003-10-20",
                "end_date": "2003-10-28",
                "place_location": "Prague",
                "country": "Czech-Republic",
            },
            {
                "start_date": "2012-07-10",
                "end_date": "2012-07-13",
                "place_location": "Fruska-Gora",
                "country": "Serbia",
            },
            {
                "start_date": "2015-09-01",
                "end_date": "2015-09-07",
                "place_location": "ISKCON-Ljubljana",
                "country": "Slovenia",
            },
        ],
        "media": [
            {
                "id": 2335,
                "date": "2015-08-27",
                "category": "Srimad-bhagavatam",
                "place": None,
                "country": "Sweden",
                "tag": "3.6.6",
                "title": "SB 3.6.6 class",
            },
        ],
        "options": {
            "place_location": [
                "Oslo",
                "Berlin",
                "Radhadesh",
                "Prague",
                "Fruska-Gora",
                "ISKCON-Ljubljana",
                "Farma-Krishna-Dvur",
                "Zoom-livestream",
            ],
            "country": [
                "Norway",
                "Germany",
                "Belgium",
                "Czech-republic",
                "Serbia",
                "Slovenia",
                "Sweden",
                "Finland",
            ],
            "category": [c["category"] for c in categories],
            "language": ["English", "German", "Dutch", "Czech"],
        },
    }
