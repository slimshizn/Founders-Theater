"""Movie box sets: the movies TMDB lists in one collection, gathered per library.

Routes are Silo /api/v2: GET /catalog/box-sets (search), GET /catalog/box-sets/{id}
(?library_id, one box set with its movies in release order) and
GET /catalog/items/{id}/box-set (the box set a movie's page shows). A library
reports box_sets_enabled in GET /user/libraries; servers without box sets have
none on, so they are never asked. Wording follows Silo's Android TV app
(BoxSetModels.kt). Every reader tolerates missing fields and returns plain dicts.
"""

from urllib.parse import quote

from . import present
from .svc_browse import _dict, _get, _int, _list, _text

SEARCH_LIMIT = 5


def box_set(entry):
    """A box set as {"id", "library_id", "title", "overview", "poster_url", "backdrop_url",
    "movie_count", "played_count", "first_year", "last_year"}, or None without an id or library."""
    entry = _dict(entry)
    box_set_id, library_id = _text(entry.get("id")), _text(entry.get("library_id"))
    if not box_set_id or not library_id:
        return None
    found = {"id": box_set_id, "library_id": library_id, "title": _text(entry.get("title")) or "Box set",
             "overview": _text(entry.get("overview")), "poster_url": _text(entry.get("poster_url")),
             "backdrop_url": _text(entry.get("backdrop_url"))}
    for key in ("movie_count", "played_count"):
        found[key] = max(0, _int(entry.get(key)) or 0)
    for key in ("first_year", "last_year"):
        year = _int(entry.get(key)) or 0
        found[key] = year if year > 0 else None
    return found


def detail(entry):
    """A box set with its movies: {"box_set", "items"}, or None."""
    entry = _dict(entry)
    found = box_set(entry)
    if found is None:
        return None
    return {"box_set": found, "items": present.video_items(_list(entry.get("items")))}


def search(client, query, limit=SEARCH_LIMIT):
    """Box sets whose name or movies match ``query``, best match first."""
    response = _get(client, "/catalog/box-sets", q=query, limit=limit)
    return [found for found in (box_set(entry) for entry in _list(response.get("items"))) if found]


def load(client, box_set_id, library_id):
    """One box set of one library with its movies, or None when the server has none to show."""
    return detail(_get(client, "/catalog/box-sets/" + quote(str(box_set_id), safe=""), library_id=library_id))


def for_item(client, content_id):
    """The box set a movie's page shows, or None when the movie is in none."""
    response = _get(client, "/catalog/items/{}/box-set".format(quote(str(content_id), safe="")))
    return detail(response.get("box_set"))


# ------------------------------------------------------------ presentation


def film_count(count):
    return "1 film" if count == 1 else "{} films".format(count)


def years_label(found):
    """"2001 – 2011", one year when the box set spans one, or "" without a year."""
    first, last = found.get("first_year"), found.get("last_year")
    if first and last and first != last:
        return "{} – {}".format(first, last)
    return str(first or last or "")


def card_caption(found):
    """A search card's caption: watched progress once started, else the years or the film count."""
    if found["played_count"] > 0:
        return "{} / {} watched".format(found["played_count"], found["movie_count"])
    return years_label(found) or film_count(found["movie_count"])


def watched_percent(found):
    """Whole percent of the box set watched for a card's bar, or 0 before the first movie."""
    if found["played_count"] <= 0 or found["movie_count"] <= 0:
        return 0
    return min(100, found["played_count"] * 100 // found["movie_count"])


def hero_facts(found):
    """The box set page's facts line: "Box set · 8 films · 2001 – 2011 · 5 of 8 watched"."""
    parts = ["Box set", film_count(found["movie_count"]), years_label(found)]
    if found["played_count"] > 0:
        parts.append("{} of {} watched".format(found["played_count"], found["movie_count"]))
    return " · ".join(part for part in parts if part)


def next_movie(items):
    """What Play starts: the first movie in release order not yet played, or the first once all are."""
    return next((item for item in items if not present.is_watched(item)), items[0] if items else None)


def play_label(found, items):
    """"Play <title>", or "Continue · <title>" once the box set or its next movie is started."""
    movie = next_movie(items)
    if movie is None:
        return ""
    title = _text(movie.get("title")) or "movie"
    started = 0 < found["played_count"] < found["movie_count"] or present.progress_percent(movie) > 0
    return ("Continue · " if started else "Play ") + title
