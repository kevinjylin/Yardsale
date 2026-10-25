"""Students: school from email, proving the address, same-school trading, and meeting points.

Nothing here sends mail or calls Claude: the campus lookup is faked everywhere.
"""

from __future__ import annotations

import pytest

from yardsale import campus, llm, schools, students
from yardsale.meetup import Spot
from yardsale.schools import SchoolError
from yardsale.store import PROPOSED, Listing, Store
from yardsale.students import StudentError

ALICE, BOB, CARL = "asi1:alice", "asi1:bob", "asi1:carl"

# Berkeley spots in the shape Claude returns them: named, best first, no coordinates. The dorm
# is first so the tests prove a residence loses to the ordering rather than riding on it.
UNIT_1 = Spot("Unit 1 Residence Hall", kind="dormitory", indoor=True)
SPROUL = Spot("Sproul Plaza", kind="plaza")
CAFE = Spot("Free Speech Movement Cafe", kind="cafe", indoor=True)
LIBRARY = Spot("Doe Library", kind="library", indoor=True)
GLADE = Spot("Memorial Glade", kind="park")
CAMPUS = [UNIT_1, SPROUL, CAFE, LIBRARY, GLADE]


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def db(clock: Clock):
    store = Store(clock=clock)
    store.save_campus("berkeley.edu", CAMPUS)
    yield store
    store.close()


def enrol(db: Store, owner: str, email: str):
    return students.join(db, owner, email)


def listing_of(owner: str, item_id: str = "item1") -> Listing:
    return Listing(
        item_id=item_id,
        owner=owner,
        seller_id="payout",
        listing_ref="r",
        status=PROPOSED,
        title="Mini fridge",
        asking_cents=6000,
        floor_cents=4000,
    )


# --- School from an email -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("email", "key"),
    [
        ("rahul@berkeley.edu", "berkeley.edu"),
        ("sam@cs.berkeley.edu", "berkeley.edu"),  # a department is the same school
        ("a@eecs.mit.edu", "mit.edu"),
        ("b@student.ucla.edu", "ucla.edu"),
        ("  C@Berkeley.EDU  ", "berkeley.edu"),  # trimmed and lowercased
        ("d@ox.ac.uk", "ox.ac.uk"),  # the longer suffix wins over .uk
        ("e@sydney.edu.au", "sydney.edu.au"),
    ],
)
def test_the_school_key_comes_from_the_domain(email: str, key: str) -> None:
    assert schools.school_key(email) == key


@pytest.mark.parametrize(
    "email", ["rahul@gmail.com", "a@company.co", "b@berkeley.com", "c@.edu", "not-an-email", ""]
)
def test_a_non_school_address_is_refused(email: str) -> None:
    with pytest.raises(SchoolError):
        schools.school_key(email)


def test_two_departments_are_the_same_school() -> None:
    one = schools.school_key("a@cs.berkeley.edu")
    two = schools.school_key("b@math.berkeley.edu")
    assert schools.same_school(one, two)


def test_different_schools_are_not_the_same() -> None:
    assert not schools.same_school(
        schools.school_key("a@berkeley.edu"), schools.school_key("b@stanford.edu")
    )


def test_an_empty_school_never_matches_itself() -> None:
    """Otherwise two unverified students would count as classmates."""
    assert not schools.same_school("", "")


def test_the_school_reads_nicely() -> None:
    assert schools.school_label("berkeley.edu") == "Berkeley"
    assert schools.school_label("stanford.edu") == "Stanford"
    assert schools.school_label("ox.ac.uk") == "Oxford"
    assert schools.school_label("cal-poly.edu") == "Cal Poly"


@pytest.mark.parametrize(
    ("key", "label"),
    [
        ("ucr.edu", "UCR"),
        ("ucla.edu", "UCLA"),
        ("ucsd.edu", "UCSD"),
        ("mit.edu", "MIT"),
        ("usc.edu", "USC"),
        ("nyu.edu", "NYU"),
        ("ucl.ac.uk", "UCL"),
    ],
)
def test_school_initials_read_as_initials(key: str, label: str) -> None:
    """On October 25 a UCR student was told "You're in, at Ucr"."""
    assert schools.school_label(key) == label


# --- Same school only ----------------------------------------------------------------------


def test_classmates_can_see_each_others_listings(db: Store) -> None:
    enrol(db, ALICE, "alice@berkeley.edu")
    enrol(db, BOB, "bob@cs.berkeley.edu")
    assert students.visible_to(db, listing_of(ALICE), BOB)
    assert students.may_trade(db, listing_of(ALICE), BOB)


def test_another_university_cannot_see_the_listing(db: Store) -> None:
    enrol(db, ALICE, "alice@berkeley.edu")
    enrol(db, CARL, "carl@stanford.edu")
    assert not students.visible_to(db, listing_of(ALICE), CARL)
    with pytest.raises(StudentError, match="different university"):
        students.check_may_trade(db, listing_of(ALICE), CARL)


def test_somebody_with_no_school_email_sees_nothing(db: Store) -> None:
    """Not giving a school must not be a way around the same-school rule."""
    enrol(db, ALICE, "alice@berkeley.edu")
    assert not students.visible_to(db, listing_of(ALICE), BOB)
    with pytest.raises(StudentError, match="school email"):
        students.check_may_trade(db, listing_of(ALICE), BOB)


def test_a_stranger_with_no_email_sees_nothing(db: Store) -> None:
    enrol(db, ALICE, "alice@berkeley.edu")
    assert not students.visible_to(db, listing_of(ALICE), "asi1:stranger")


def test_a_seller_always_sees_their_own_listing(db: Store) -> None:
    """Even before giving an email, so they aren't locked out of something they posted."""
    assert students.visible_to(db, listing_of(ALICE), ALICE)


def test_nobody_buys_their_own_item(db: Store) -> None:
    enrol(db, ALICE, "alice@berkeley.edu")
    assert not students.may_trade(db, listing_of(ALICE), ALICE)
    with pytest.raises(StudentError, match="your own listing"):
        students.check_may_trade(db, listing_of(ALICE), ALICE)


def test_search_results_are_filtered_to_one_school(db: Store) -> None:
    enrol(db, ALICE, "alice@berkeley.edu")
    enrol(db, BOB, "bob@berkeley.edu")
    enrol(db, CARL, "carl@stanford.edu")
    everything = [listing_of(ALICE, "a"), listing_of(CARL, "c")]
    assert [x.item_id for x in students.listings_for(db, everything, BOB)] == ["a"]
    assert [x.item_id for x in students.listings_for(db, everything, CARL)] == ["c"]


# --- The campus cache -----------------------------------------------------------------------


def test_a_campus_is_stored_and_read_back(db: Store) -> None:
    assert db.campus_loaded("berkeley.edu")
    assert {s.name for s in db.campus("berkeley.edu")} == {s.name for s in CAMPUS}


def test_an_unknown_campus_is_empty_not_an_error(db: Store) -> None:
    assert db.campus("stanford.edu") == []
    assert not db.campus_loaded("stanford.edu")


def test_reloading_a_campus_replaces_it(db: Store) -> None:
    db.save_campus("berkeley.edu", [CAFE])
    assert [s.name for s in db.campus("berkeley.edu")] == ["Free Speech Movement Cafe"]


def test_schools_people_signed_up_from_are_warmed(db: Store) -> None:
    enrol(db, ALICE, "alice@berkeley.edu")
    assert db.schools_with_students() == ["berkeley.edu"]


def test_a_campus_and_a_student_survive_a_restart(clock: Clock, tmp_path) -> None:
    path = tmp_path / "campus.sqlite3"
    first = Store(path, clock=clock)
    first.save_campus("berkeley.edu", CAMPUS)
    enrol(first, ALICE, "alice@berkeley.edu")
    first.close()

    again = Store(path, clock=clock)
    try:
        student = again.student(ALICE)
        assert student is not None and student.school == "berkeley.edu"
        assert again.count_campus("berkeley.edu") == len(CAMPUS)
    finally:
        again.close()


# --- Claude names the campus ----------------------------------------------------------------


def naming(*places: Spot):
    """A stand-in for the Claude call, so no test spends money or reaches the network."""
    asked: list[str] = []

    async def name_spots(school: str) -> list[Spot]:
        asked.append(school)
        return list(places)

    return name_spots, asked


async def test_a_school_key_is_all_claude_needs() -> None:
    name_spots, asked = naming(SPROUL, CAFE, LIBRARY)
    spots = await campus.find_and_load("berkeley.edu", name_spots)
    assert asked == ["berkeley.edu"]
    assert [s.name for s in spots] == ["Sproul Plaza", "Free Speech Movement Cafe", "Doe Library"]


async def test_the_order_claude_gives_is_the_ranking() -> None:
    """Claude names the places best first, so nothing re-sorts them afterwards."""
    name_spots, _ = naming(LIBRARY, SPROUL, CAFE)
    spots = await campus.find_and_load("berkeley.edu", name_spots)
    assert [s.name for s in spots] == ["Doe Library", "Sproul Plaza", "Free Speech Movement Cafe"]


async def test_a_campus_arrives_without_coordinates() -> None:
    """A place lookup fills these in later; a name is enough to meet somebody."""
    name_spots, _ = naming(SPROUL)
    spot = (await campus.find_and_load("berkeley.edu", name_spots))[0]
    assert (spot.latitude, spot.longitude) == (0.0, 0.0)


async def test_a_university_claude_does_not_know_is_reported() -> None:
    name_spots, _ = naming()
    with pytest.raises(campus.CampusError, match="don't know this university's campus"):
        await campus.load("nowhere.edu", name_spots)
    assert await campus.find_and_load("nowhere.edu", name_spots) == []


async def test_claude_being_unavailable_is_not_fatal() -> None:
    async def no_key(school: str) -> list[Spot]:
        raise llm.LLMError("ANTHROPIC_API_KEY is not set on the server")

    with pytest.raises(campus.CampusError, match="ANTHROPIC_API_KEY"):
        await campus.load("berkeley.edu", no_key)
    assert await campus.find_and_load("berkeley.edu", no_key) == []


async def test_any_other_failure_is_not_fatal() -> None:
    async def broken(school: str) -> list[Spot]:
        raise RuntimeError("boom")

    assert await campus.find_and_load("berkeley.edu", broken) == []


def test_a_place_with_no_name_is_dropped() -> None:
    """The name is the whole instruction, so there's nothing to tell a student without it."""
    assert campus.usable([Spot("   ", kind="cafe"), CAFE]) == [CAFE]


def test_the_same_place_twice_is_kept_once() -> None:
    twice = [CAFE, Spot("  free speech   movement cafe ", kind="landmark")]
    assert [s.name for s in campus.usable(twice)] == ["Free Speech Movement Cafe"]


def test_a_school_key_becomes_something_searchable() -> None:
    assert campus.school_label_for("berkeley.edu") == "Berkeley"
    assert campus.school_label_for("ox.ac.uk") == "Ox"
    assert campus.school_label_for("texas-am.edu") == "Texas Am"


# --- The Claude call itself ------------------------------------------------------------------


async def test_claude_is_asked_about_the_named_university(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, str]] = []

    async def fake_ask(system: str, content: list[dict], output: type):
        sent.append((system, content[0]["text"]))
        return output(
            recognised=True,
            places=[
                {"name": "Sproul Plaza", "kind": "plaza", "indoor": False},
                {"name": "Free Speech Movement Cafe", "kind": "cafe", "indoor": True},
            ],
        )

    monkeypatch.setattr(campus.llm, "ask", fake_ask)
    spots = await campus.name_spots_with_claude("berkeley.edu")
    assert "Berkeley university" in sent[0][1]
    assert spots == [SPROUL, CAFE]


async def test_claude_saying_it_does_not_know_names_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rather than inventing a plaza on a campus it's never heard of."""

    async def fake_ask(system: str, content: list[dict], output: type):
        return output(recognised=False, places=[])

    monkeypatch.setattr(campus.llm, "ask", fake_ask)
    assert await campus.name_spots_with_claude("nowhere.edu") == []


# --- Warming a campus ------------------------------------------------------------------------


async def test_warming_a_campus_caches_it_once(db: Store) -> None:
    name_spots, asked = naming(CAFE, LIBRARY)
    assert await students.warm_campus(db, "stanford.edu", name_spots) == 2
    assert await students.warm_campus(db, "stanford.edu", name_spots) == 2
    assert len(asked) == 1  # the second call reads the store, not Claude


async def test_warming_an_unknown_campus_does_not_cache_emptiness(db: Store) -> None:
    name_spots, _ = naming()
    assert await students.warm_campus(db, "nowhere.edu", name_spots) == 0
    assert not db.campus_loaded("nowhere.edu")  # so it can be retried later


async def test_a_cached_campus_is_not_fetched_again(db: Store) -> None:
    name_spots, asked = naming(CAFE)
    enrol(db, ALICE, "alice@berkeley.edu")  # berkeley is already cached by the fixture
    enrol(db, CARL, "carl@stanford.edu")
    await students.warm_known_campuses(db, name_spots)
    assert asked == ["stanford.edu"]


def test_the_stored_order_is_the_order_claude_gave(db: Store) -> None:
    """The store is between Claude and the meetup, so it mustn't re-sort the ranking."""
    assert [s.name for s in db.campus("berkeley.edu")] == [s.name for s in CAMPUS]


# --- One meeting point on campus ------------------------------------------------------------


def test_the_meetup_is_the_first_public_place_on_campus(db: Store) -> None:
    meeting = students.where_to_meet(db, "berkeley.edu")
    assert meeting.spot.name == "Sproul Plaza"
    assert not meeting.spot.private


def test_a_meetup_is_never_somebody_s_dorm(db: Store) -> None:
    """The point of the feature: never send a student to where anyone lives."""
    assert students.where_to_meet(db, "berkeley.edu").spot.name != UNIT_1.name
    for meeting in students.other_places(db, "berkeley.edu", limit=10):
        assert not meeting.spot.private
        assert meeting.spot.name != UNIT_1.name


def test_an_unmapped_campus_says_so(db: Store) -> None:
    with pytest.raises(StudentError, match="map of your campus"):
        students.where_to_meet(db, "stanford.edu")
    assert students.other_places(db, "stanford.edu") == []


def test_a_campus_with_no_places_has_nowhere_to_meet() -> None:
    from yardsale.meetup import choose

    assert choose([]) is None


def test_a_campus_of_only_dorms_has_nowhere_to_meet() -> None:
    from yardsale.meetup import choose

    assert choose([UNIT_1, Spot("Foothill Residence Hall", kind="dormitory")]) is None


def test_a_meetup_reads_with_its_opening_hours() -> None:
    from yardsale.meetup import Meetup, describe

    assert describe(Meetup(SPROUL)) == "Sproul Plaza"
    open_late = Spot("Doe Library", kind="library", opening_hours="Mo-Fr 08:00-22:00")
    assert describe(Meetup(open_late)) == "Doe Library (open Mo-Fr 08:00-22:00)"


def test_nobody_is_asked_where_they_live(db: Store) -> None:
    """A Student holds an email and a school, and nothing about where they are."""
    enrol(db, ALICE, "alice@berkeley.edu")
    student = db.student(ALICE)
    assert student is not None
    fields = set(vars(student))
    assert fields == {"owner", "email", "school", "joined_at"}
