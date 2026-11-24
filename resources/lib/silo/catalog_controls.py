"""Sort and filter choices for a catalog grid: Home's library grid and a collection's page.

Android has a sort panel and a filter panel (TvBrowseSortPanel, TvBrowseFilterPanel) on
both; here they are Kodi select dialogs. A sort is a /catalog sort term, where a leading
"-" sorts descending. Filters are facet selections, {facet: [values]}, which lower to the
rule groups the /catalog ``groups`` parameter takes.
"""

from .ui_common import search_heading

# (sort value, dialog label, chip label); values are the server's catalog sort fields.
SORTS = (("title", "Title A–Z", "Title"), ("-added_at", "Recently added", "Recently added"),
         ("-release_date", "Release date", "Release date"), ("-year", "Year", "Year"),
         ("-rating_imdb", "Rating", "Rating"), ("-runtime", "Runtime", "Runtime"),
         ("-date_viewed", "Recently watched", "Recently watched"), ("-plays", "Most played", "Most played"))
# A collection opens in its own order: no sort term, and no direction to reverse.
COLLECTION_SORTS = (("", "Collection order", "Collection order"),) + SORTS

# Facet filters in Android TvCatalogFacet order. Decade, dynamic range and watch status are fixed
# vocabularies; the others come from GET /catalog/filters.
FACETS = (("genre", "Genre"), ("decade", "Decade"), ("watch_status", "Watch status"),
          ("content_rating", "Content rating"), ("resolution", "Resolution"),
          ("dynamic_range", "Dynamic range"), ("studio", "Studio"), ("network", "Network"),
          ("country", "Country"))
FACET_SOURCES = {"genre": "genres", "content_rating": "content_ratings", "studio": "studios",
                 "network": "networks", "country": "countries"}
# (facet, rule field, operator) for facets that lower to one "any" group of value rules.
VALUE_RULES = (("genre", "genre", "contains"), ("studio", "studio", "is"), ("network", "network", "is"),
               ("country", "country", "is"), ("content_rating", "content_rating", "is"),
               ("resolution", "resolution", "is"))
DECADES = (2020, 2010, 2000, 1990, 1980, 1970, 1960)
DYNAMIC_RANGES = (("hdr", "HDR"), ("dolby_vision", "Dolby Vision"))
# (value, label, (rule field, rule value)); watch status is single-select.
WATCH_STATUSES = (("unwatched", "Unwatched", ("watched", False)), ("in_progress", "In progress", ("in_progress", True)),
                  ("watched", "Watched", ("watched", True)), ("favorited", "Favorited", ("favorited", True)),
                  ("watchlist", "Watchlist", ("in_watchlist", True)))
# Long server lists offer a search first (GET /catalog/filters/search).
SEARCHABLE_FACETS = frozenset(["genre", "content_rating", "studio", "network", "country"])
FACET_SEARCH_MIN, FACET_SEARCH_LIMIT = 60, 100


# ------------------------------------------------------------------ sorting

def sort_term(sort, reversed_):
    """The catalog sort term: a leading "-" sorts descending, so reversing flips the sign."""
    if not sort or not reversed_:
        return sort
    return sort[1:] if sort.startswith("-") else "-" + sort


def sort_chip(sorts, sort, reversed_):
    chip = next(chip for value, _label, chip in sorts if value == sort)
    return chip + " (reversed)" if reversed_ and sort else chip


def choose_sort(dialog, sorts, sort, reversed_):
    """Pick a sort, or the last row to flip the current one's direction (collection order has
    none). Returns (sort, reversed) when the choice changed, else None."""
    values = [value for value, _label, _chip in sorts]
    current = values.index(sort)
    labels = [label for _value, label, _chip in sorts]
    if sort:
        labels.append("Reverse order: " + ("On" if reversed_ else "Off"))
    choice = dialog.select("Sort by", labels, preselect=current)
    if not isinstance(choice, int) or choice < 0 or choice >= len(labels) or choice == current:
        return None
    if choice == len(values):
        return sort, not reversed_
    return values[choice], False  # A new sort starts in its natural direction.


# ------------------------------------------------------------------ filters

def filter_groups(filters):
    """Lower facet selections to /catalog rule groups, as Android's TvCatalogFacetSelection does."""
    groups = []
    for facet, field, op in VALUE_RULES:
        values = sorted(filters.get(facet) or ())
        if values:
            groups.append({"match": "any", "rules": [{"field": field, "op": op, "value": value}
                                                     for value in values]})
    decades = sorted(int(value) for value in filters.get("decade") or ())
    if decades:
        groups.append({"match": "any", "rules": [{"field": "decade", "op": "is", "value": decade}
                                                 for decade in decades]})
    ranges = [value for value, _label in DYNAMIC_RANGES if value in (filters.get("dynamic_range") or ())]
    if ranges:
        groups.append({"match": "any", "rules": [{"field": value, "op": "is", "value": True} for value in ranges]})
    status = next((rule for value, _label, rule in WATCH_STATUSES
                   if value in (filters.get("watch_status") or ())), None)
    if status:
        groups.append({"match": "all", "rules": [{"field": status[0], "op": "is", "value": status[1]}]})
    return groups


def active_facets(filters):
    return sum(1 for facet, _label in FACETS if filters.get(facet))


def facet_values(facet, options):
    """(value, label) pairs the facet offers; facets without any are not listed."""
    if facet == "decade":
        return [(str(decade), "{}s".format(decade)) for decade in DECADES]
    if facet == "dynamic_range":
        return list(DYNAMIC_RANGES)
    if facet == "watch_status":
        return [(value, label) for value, label, _rule in WATCH_STATUSES]
    if facet == "resolution":
        technical = options.get("technical") if isinstance(options.get("technical"), dict) else {}
        values = technical.get("resolutions")
    else:
        values = options.get(FACET_SOURCES[facet])
    return [(str(value), str(value)) for value in values or () if value not in (None, "")]


def choose_filters(dialog, filters, options, search):
    """The Filters dialog over ``options`` (a GET /catalog/filters answer). It edits ``filters``
    in place and returns whether they changed. ``search(facet, text, current)`` answers a
    long facet's search with (value, label) pairs; None offers no search."""
    changed = False
    while True:
        facets = [(facet, label) for facet, label in FACETS if facet_values(facet, options)]
        labels = []
        for facet, label in facets:
            count = len(filters.get(facet) or ())
            labels.append("{} ({})".format(label, count) if count else label)
        if active_facets(filters):
            labels.append("Clear filters")
        choice = dialog.select("Filters", labels)
        if not isinstance(choice, int) or not 0 <= choice < len(labels):
            return changed
        if choice == len(facets):
            filters.clear()
            changed = True
            continue
        facet, label = facets[choice]
        if edit_facet(dialog, filters, facet, label, facet_values(facet, options), search):
            changed = True


def edit_facet(dialog, filters, facet, label, values, search):
    """Pick the facet's values; True when the selection changed."""
    current = set(filters.get(facet) or ())
    if facet == "watch_status":
        names = ["Any"] + [name for _value, name in values]
        preselect = next((index + 1 for index, (value, _name) in enumerate(values) if value in current), 0)
        choice = dialog.select(label, names, preselect=preselect)
        if not isinstance(choice, int) or not 0 <= choice < len(names):
            return False
        chosen = {values[choice - 1][0]} if choice else set()
    else:
        if search is not None and facet in SEARCHABLE_FACETS and len(values) > FACET_SEARCH_MIN:
            text = (dialog.input(search_heading("Search " + label.lower(), "leave empty for all")) or "").strip()
            if text:
                values = search(facet, text, current)
        if not values:
            return False
        selected = [index for index, (value, _name) in enumerate(values) if value in current]
        picked = dialog.multiselect(label, [name for _value, name in values], preselect=selected)
        if picked is None:
            return False
        listed = set(value for value, _name in values)
        chosen = (current - listed) | set(values[index][0] for index in picked if 0 <= index < len(values))
    if chosen == current:
        return False
    if chosen:
        filters[facet] = sorted(chosen)
    else:
        filters.pop(facet, None)
    return True


def facet_search(client, scope, facet, text, current):
    """GET /catalog/filters/search within ``scope`` (the filters read's params); current
    selections stay listed first."""
    params = dict(scope, facet=facet, q=text, limit=FACET_SEARCH_LIMIT)
    response = client.request("GET", "/catalog/filters/search", params=params) or {}
    found = [entry.get("value") for entry in response.get("values") or () if isinstance(entry, dict)]
    if not found:
        found = list(response.get("matches") or ())
    values = sorted(current) + [str(value) for value in found if value and str(value) not in current]
    return [(value, value) for value in values]
