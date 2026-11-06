"""Box set reads and wording: routes, tolerant parsing and the Android TV labels."""

import importlib
import unittest
from unittest.mock import MagicMock

svc_boxsets = importlib.import_module("resources.lib.silo.svc_boxsets")


def entry(**changes):
    value = {"id": 1241, "library_id": "2", "title": "Example Box Set", "overview": "Eight films.",
             "poster_url": "/artwork/p.jpg", "backdrop_url": "/artwork/b.jpg", "movie_count": 8,
             "played_count": 0, "first_year": 2001, "last_year": 2011}
    value.update(changes)
    return value


def movie(index, played=False, position=None):
    item = {"content_id": "movie:{}".format(index), "type": "movie", "title": "Film {}".format(index),
            "runtime": 100, "user_state": {"played": played}}
    if position:
        item["user_data"] = {"position_seconds": position}
    return item


class ParsingTests(unittest.TestCase):
    def test_a_box_set_needs_an_id_and_a_library(self):
        self.assertIsNone(svc_boxsets.box_set(entry(id=None)))
        self.assertIsNone(svc_boxsets.box_set(entry(library_id="")))
        self.assertIsNone(svc_boxsets.box_set("not a box set"))
        found = svc_boxsets.box_set(entry(movie_count="8", played_count=-1, first_year=0, title=""))
        self.assertEqual((found["id"], found["library_id"], found["title"]), ("1241", "2", "Box set"))
        self.assertEqual((found["movie_count"], found["played_count"], found["first_year"]), (8, 0, None))

    def test_detail_keeps_only_video_items(self):
        found = svc_boxsets.detail(dict(entry(), items=[movie(1), {"title": "no id"}, "junk", movie(2)]))
        self.assertEqual([item["content_id"] for item in found["items"]], ["movie:1", "movie:2"])
        self.assertIsNone(svc_boxsets.detail(None))

    def test_routes(self):
        client = MagicMock()
        client.request.return_value = {"items": [entry(), {"id": 9}]}
        self.assertEqual([found["id"] for found in svc_boxsets.search(client, "harry potter")], ["1241"])
        client.request.assert_called_with("GET", "/catalog/box-sets", params={"q": "harry potter", "limit": 5})
        client.request.return_value = dict(entry(), items=[movie(1)])
        self.assertEqual(svc_boxsets.load(client, "1241", "2")["box_set"]["title"], "Example Box Set")
        client.request.assert_called_with("GET", "/catalog/box-sets/1241", params={"library_id": "2"})
        client.request.return_value = {"box_set": dict(entry(), items=[movie(1)])}
        self.assertEqual(len(svc_boxsets.for_item(client, "movie:a/b")["items"]), 1)
        client.request.assert_called_with("GET", "/catalog/items/movie%3Aa%2Fb/box-set", params=None)
        client.request.return_value = {}  # The movie is in no box set.
        self.assertIsNone(svc_boxsets.for_item(client, "movie:1"))


class WordingTests(unittest.TestCase):
    def test_years_counts_and_captions(self):
        found = svc_boxsets.box_set(entry())
        self.assertEqual(svc_boxsets.years_label(found), "2001 – 2011")
        self.assertEqual(svc_boxsets.years_label(dict(found, last_year=2001)), "2001")
        self.assertEqual(svc_boxsets.years_label(dict(found, first_year=None, last_year=None)), "")
        self.assertEqual((svc_boxsets.film_count(1), svc_boxsets.film_count(8)), ("1 film", "8 films"))
        self.assertEqual(svc_boxsets.card_caption(found), "2001 – 2011")
        self.assertEqual(svc_boxsets.card_caption(dict(found, first_year=None, last_year=None)), "8 films")
        self.assertEqual(svc_boxsets.card_caption(dict(found, played_count=5)), "5 / 8 watched")
        self.assertEqual(svc_boxsets.watched_percent(found), 0)
        self.assertEqual(svc_boxsets.watched_percent(dict(found, played_count=5)), 62)

    def test_hero_facts_add_watched_once_started(self):
        found = svc_boxsets.box_set(entry())
        self.assertEqual(svc_boxsets.hero_facts(found), "Box set · 8 films · 2001 – 2011")
        self.assertEqual(svc_boxsets.hero_facts(dict(found, played_count=5)),
                         "Box set · 8 films · 2001 – 2011 · 5 of 8 watched")

    def test_play_picks_the_first_unwatched_movie(self):
        found = svc_boxsets.box_set(entry(movie_count=3))
        fresh = [movie(1), movie(2), movie(3)]
        self.assertEqual(svc_boxsets.next_movie(fresh)["content_id"], "movie:1")
        self.assertEqual(svc_boxsets.play_label(found, fresh), "Play Film 1")
        started = [movie(1, played=True), movie(2), movie(3)]
        self.assertEqual(svc_boxsets.next_movie(started)["content_id"], "movie:2")
        self.assertEqual(svc_boxsets.play_label(dict(found, played_count=1), started), "Continue · Film 2")
        partway = [movie(1, position=1200), movie(2), movie(3)]
        self.assertEqual(svc_boxsets.play_label(found, partway), "Continue · Film 1")
        done = [movie(1, played=True), movie(2, played=True)]
        self.assertEqual(svc_boxsets.next_movie(done)["content_id"], "movie:1")
        self.assertEqual(svc_boxsets.play_label(dict(found, movie_count=2, played_count=2), done), "Play Film 1")
        self.assertIsNone(svc_boxsets.next_movie([]))
        self.assertEqual(svc_boxsets.play_label(found, []), "")


if __name__ == "__main__":
    unittest.main()
