"""Mock TMDB API responses for testing."""

# TMDB Search Response for "Arrested Development"
TMDB_SEARCH_ARRESTED_DEVELOPMENT = {
    "page": 1,
    "results": [
        {
            "id": 4589,
            "name": "Arrested Development",
            "original_name": "Arrested Development",
            "first_air_date": "2003-11-02",
            "origin_country": ["US"],
            "overview": "The story of a wealthy family that lost everything...",
        }
    ],
    "total_pages": 1,
    "total_results": 1,
}

# TMDB Search Response for "The Office"
TMDB_SEARCH_THE_OFFICE = {
    "page": 1,
    "results": [
        {
            "id": 2316,
            "name": "The Office",
            "original_name": "The Office",
            "first_air_date": "2005-03-24",
            "origin_country": ["US"],
        }
    ],
}

# TMDB Search Response for "Office" (without "The")
TMDB_SEARCH_OFFICE = {
    "page": 1,
    "results": [
        {
            "id": 2316,
            "name": "The Office",
            "original_name": "The Office",
            "first_air_date": "2005-03-24",
            "origin_country": ["US"],
        }
    ],
}

# TMDB Search Response for "Star Trek: TNG"
TMDB_SEARCH_STAR_TREK = {
    "page": 1,
    "results": [
        {
            "id": 655,
            "name": "Star Trek: The Next Generation",
            "original_name": "Star Trek: The Next Generation",
            "first_air_date": "1987-09-28",
            "origin_country": ["US"],
        }
    ],
}

# TMDB Search Response for "Breaking Bad"
TMDB_SEARCH_BREAKING_BAD = {
    "page": 1,
    "results": [
        {
            "id": 1396,
            "name": "Breaking Bad",
            "original_name": "Breaking Bad",
            "first_air_date": "2008-01-20",
            "origin_country": ["US"],
        }
    ],
}

# TMDB Season Details for Season 1 with 3 episodes
TMDB_SEASON_DETAILS_S01_3EP = {
    "id": 3572,
    "air_date": "2003-11-02",
    "episodes": [
        {
            "air_date": "2003-11-02",
            "episode_number": 1,
            "id": 61723,
            "name": "Pilot",
            "overview": "Meet the Bluths...",
            "season_number": 1,
        },
        {
            "air_date": "2003-11-09",
            "episode_number": 2,
            "id": 61724,
            "name": "Top Banana",
            "overview": "Michael has a one-night stand...",
            "season_number": 1,
        },
        {
            "air_date": "2003-11-16",
            "episode_number": 3,
            "id": 61725,
            "name": "Bringing Up Buster",
            "overview": "Michael sets Buster up on a date...",
            "season_number": 1,
        },
    ],
    "name": "Season 1",
    "overview": "",
    "season_number": 1,
}

# TMDB Season Details for Breaking Bad Season 1 (7 episodes)
TMDB_SEASON_DETAILS_BREAKING_BAD_S01 = {
    "id": 3577,
    "air_date": "2008-01-20",
    "episodes": [
        {"episode_number": i, "name": f"Episode {i}", "season_number": 1} for i in range(1, 8)
    ],
    "name": "Season 1",
    "season_number": 1,
}

# Empty TMDB Search Response
TMDB_SEARCH_EMPTY = {"page": 1, "results": [], "total_pages": 0, "total_results": 0}

# Test cases for variation testing
VARIATION_TEST_CASES = [
    ("Arrested Development", "4589", "Clean name"),
    ("The Office", "2316", "Name with 'The' prefix"),
    ("Star Trek: TNG", "655", "Name with colon"),
    ("Breaking Bad S1", "1396", "Name with season indicator"),
    ("Fargo (2014)", "60622", "Name with year"),
]
