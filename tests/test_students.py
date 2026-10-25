"""Students: school from email, proving the address, same-school trading, and meeting points.

Nothing here sends mail or reaches a map service.
"""

from __future__ import annotations

import pytest

from yardsale import campus, schools, students
from yardsale.meetup import Spot
from yardsale.schools import SchoolError
from yardsale.store import PROPOSED, Listing, Store
from yardsale.students import StudentError

ALICE, BOB, CARL = "asi1:alice", "asi1:bob", "asi1:carl"

# A few real Berkeley-ish points, close enough together to reason about on foot.
UNIT_1 = Spot("Unit 1 Residence Hall", 37.8679, -122.2553, "dormitory", indoor=True)
FOOTHILL = Spot("Foothill Residence Hall", 37.8757, -122.2554, "dormitory", indoor=True)
CAFE = Spot("Free Speech Movement Cafe", 37.8723, -122.2600, "cafe", indoor=True)
LIBRARY = Spot("Doe Library", 37.8722, -122.2592, "library", indoor=True)
LAWN = Spot("Memorial Glade", 37.8735, -122.2594, "park")
FAR_CAFE = Spot("Far Side Cafe", 37.9100, -122.3300, "cafe", indoor=True)
CAMPUS = [UNIT_1, FOOTHILL, CAFE, LIBRARY, LAWN, FAR_CAFE]


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


def enrol(db: Store, owner: str, email: str, building: str | None = None):
    _, code = students.begin(db, owner, email)
    students.confirm(db, owner, code)
    if building:
        students.set_building(db, owner, building)
    return db.student(owner)


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
    assert schools.school_label("ox.ac.uk") == "Ox"


# --- Proving the address --------------------------------------------------------------------


def test_a_code_verifies_a_student(db: Store) -> None:
    student, code = students.begin(db, ALICE, "alice@berkeley.edu")
    assert not student.verified and len(code) == schools.CODE_LENGTH and code.isdigit()
    verified = students.confirm(db, ALICE, code)
    assert verified.verified and verified.school == "berkeley.edu"


def test_the_code_is_never_stored_in_the_clear(db: Store) -> None:
    _, code = students.begin(db, ALICE, "alice@berkeley.edu")
    student = db.student(ALICE)
    assert student is not None and student.code_hash is not None
    assert code not in student.code_hash


def test_a_wrong_code_does_not_verify(db: Store) -> None:
    students.begin(db, ALICE, "alice@berkeley.edu")
    with pytest.raises(StudentError, match="isn't right"):
        students.confirm(db, ALICE, "000000")
    student = db.student(ALICE)
    assert student is not None and not student.verified


def test_a_code_cannot_be_guessed_forever(db: Store) -> None:
    students.begin(db, ALICE, "alice@berkeley.edu")
    for _ in range(schools.MAX_ATTEMPTS - 1):
        with pytest.raises(StudentError, match="isn't right"):
            students.confirm(db, ALICE, "000000")
    with pytest.raises(StudentError, match="Too many wrong codes"):
        students.confirm(db, ALICE, "000000")
    with pytest.raises(StudentError, match="expired"):  # the code is gone, not just locked
        students.confirm(db, ALICE, "000000")


def test_a_code_expires(db: Store, clock: Clock) -> None:
    _, code = students.begin(db, ALICE, "alice@berkeley.edu")
    clock.advance(schools.CODE_TTL + 1)
    with pytest.raises(StudentError, match="expired"):
        students.confirm(db, ALICE, code)


def test_a_code_for_one_student_does_not_work_for_another(db: Store) -> None:
    """The hash binds the code to the address, so a stolen row can't verify someone else."""
    _, alice_code = students.begin(db, ALICE, "alice@berkeley.edu")
    students.begin(db, BOB, "bob@berkeley.edu")
    with pytest.raises(StudentError, match="isn't right"):
        students.confirm(db, BOB, alice_code)


def test_changing_email_drops_the_old_verification(db: Store) -> None:
    enrol(db, ALICE, "alice@berkeley.edu")
    students.begin(db, ALICE, "alice@stanford.edu")
    student = db.student(ALICE)
    assert student is not None
    assert not student.verified and student.school == "stanford.edu"


def test_confirming_twice_is_harmless(db: Store) -> None:
    _, code = students.begin(db, ALICE, "alice@berkeley.edu")
    students.confirm(db, ALICE, code)
    assert students.confirm(db, ALICE, code).verified


def test_an_unknown_conversation_is_asked_for_an_email(db: Store) -> None:
    with pytest.raises(StudentError, match="school email"):
        students.confirm(db, "asi1:nobody", "123456")


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


def test_an_unverified_student_sees_nothing(db: Store) -> None:
    """Skipping verification must not be a way around the same-school rule."""
    enrol(db, ALICE, "alice@berkeley.edu")
    students.begin(db, BOB, "bob@berkeley.edu")  # started, never confirmed
    assert not students.visible_to(db, listing_of(ALICE), BOB)
    with pytest.raises(StudentError, match="six-digit code"):
        students.check_may_trade(db, listing_of(ALICE), BOB)


def test_a_stranger_with_no_email_sees_nothing(db: Store) -> None:
    enrol(db, ALICE, "alice@berkeley.edu")
    assert not students.visible_to(db, listing_of(ALICE), "asi1:stranger")


def test_a_seller_always_sees_their_own_listing(db: Store) -> None:
    """Even before verifying, so they aren't locked out of something they posted."""
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


# --- Placing a student ----------------------------------------------------------------------


def test_a_building_is_matched_by_part_of_its_name(db: Store) -> None:
    enrol(db, ALICE, "alice@berkeley.edu")
    spot = students.set_building(db, ALICE, "Unit 1")
    assert spot.name == "Unit 1 Residence Hall"
    student = db.student(ALICE)
    assert student is not None and student.placed


def test_an_unknown_building_is_refused_rather_than_guessed(db: Store) -> None:
    enrol(db, ALICE, "alice@berkeley.edu")
    with pytest.raises(StudentError, match="couldn't find"):
        students.set_building(db, ALICE, "Hogwarts")


def test_a_student_must_be_verified_before_being_placed(db: Store) -> None:
    students.begin(db, ALICE, "alice@berkeley.edu")
    with pytest.raises(StudentError, match="six-digit code"):
        students.set_building(db, ALICE, "Unit 1")


def test_a_campus_with_no_map_says_so(clock: Clock) -> None:
    store = Store(clock=clock)
    try:
        enrol(store, ALICE, "alice@berkeley.edu")
        with pytest.raises(StudentError, match="map of your campus"):
            students.set_building(store, ALICE, "Unit 1")
    finally:
        store.close()


# --- Meeting points -------------------------------------------------------------------------


def test_the_meetup_lands_between_the_two_students(db: Store) -> None:
    enrol(db, ALICE, "alice@berkeley.edu", "Unit 1")
    enrol(db, BOB, "bob@berkeley.edu", "Foothill")
    meeting = students.where_to_meet(db, ALICE, BOB)
    assert meeting.spot.name in {"Free Speech Movement Cafe", "Doe Library", "Memorial Glade"}
    assert meeting.fair


def test_no_meetup_is_ever_somebody_s_dorm(db: Store) -> None:
    """The point of the feature: never send a student to where the other one lives."""
    enrol(db, ALICE, "alice@berkeley.edu", "Unit 1")
    enrol(db, BOB, "bob@berkeley.edu", "Foothill")
    for meeting in students.other_places(db, ALICE, BOB, limit=10):
        assert not meeting.spot.private
        assert meeting.spot.name not in {"Unit 1 Residence Hall", "Foothill Residence Hall"}


def test_a_far_away_place_does_not_win_on_its_bonus(db: Store) -> None:
    enrol(db, ALICE, "alice@berkeley.edu", "Unit 1")
    enrol(db, BOB, "bob@berkeley.edu", "Foothill")
    assert students.where_to_meet(db, ALICE, BOB).spot.name != "Far Side Cafe"


def test_a_cafe_beats_a_lawn_that_is_slightly_closer(db: Store) -> None:
    """A place you can wait inside is worth a short detour."""
    enrol(db, ALICE, "alice@berkeley.edu", "Unit 1")
    enrol(db, BOB, "bob@berkeley.edu", "Foothill")
    best = students.where_to_meet(db, ALICE, BOB)
    assert best.spot.kind in ("cafe", "library")


def test_an_unplaced_student_is_asked_for_their_building(db: Store) -> None:
    enrol(db, ALICE, "alice@berkeley.edu", "Unit 1")
    enrol(db, BOB, "bob@berkeley.edu")  # no building
    with pytest.raises(StudentError, match="which building"):
        students.where_to_meet(db, ALICE, BOB)
    assert students.other_places(db, ALICE, BOB) == []


def test_the_meetup_text_never_names_either_dorm(db: Store) -> None:
    from yardsale.meetup import describe

    enrol(db, ALICE, "alice@berkeley.edu", "Unit 1")
    enrol(db, BOB, "bob@berkeley.edu", "Foothill")
    text = describe(students.where_to_meet(db, ALICE, BOB))
    assert "Unit 1" not in text and "Foothill" not in text
    assert "minute walk" in text


def test_several_places_are_offered_best_first(db: Store) -> None:
    enrol(db, ALICE, "alice@berkeley.edu", "Unit 1")
    enrol(db, BOB, "bob@berkeley.edu", "Foothill")
    offered = students.other_places(db, ALICE, BOB, limit=3)
    assert 2 <= len(offered) <= 3
    walks = [m.longer_walk_metres for m in offered]
    assert walks == sorted(walks) or offered[0].spot.kind in ("cafe", "library")


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


def test_only_schools_with_verified_students_are_warmed(db: Store) -> None:
    students.begin(db, BOB, "bob@stanford.edu")  # never confirmed
    enrol(db, ALICE, "alice@berkeley.edu")
    assert db.schools_with_students() == ["berkeley.edu"]


def test_a_campus_survives_a_restart(clock: Clock, tmp_path) -> None:
    path = tmp_path / "campus.sqlite3"
    first = Store(path, clock=clock)
    first.save_campus("berkeley.edu", CAMPUS)
    enrol(first, ALICE, "alice@berkeley.edu", "Unit 1")
    first.close()

    again = Store(path, clock=clock)
    try:
        student = again.student(ALICE)
        assert student is not None and student.verified and student.placed
        assert again.count_campus("berkeley.edu") == len(CAMPUS)
    finally:
        again.close()


# --- Reading OpenStreetMap ------------------------------------------------------------------

OSM_DORM = {
    "type": "way",
    "id": 123,
    "center": {"lat": 37.8679, "lon": -122.2553},
    "tags": {"building": "dormitory", "name": "Unit 1 Residence Hall"},
}
OSM_CAFE = {
    "type": "node",
    "id": 456,
    "lat": 37.8723,
    "lon": -122.2600,
    "tags": {
        "amenity": "cafe",
        "name": "Free Speech Movement Cafe",
        "opening_hours": "Mo-Fr 08:00-18:00",
    },
}


def test_osm_elements_become_spots() -> None:
    spots = campus.parse_campus([OSM_DORM, OSM_CAFE])
    by_name = {s.name: s for s in spots}
    assert by_name["Unit 1 Residence Hall"].kind == "dormitory"
    cafe = by_name["Free Speech Movement Cafe"]
    assert cafe.kind == "cafe" and cafe.indoor and cafe.opening_hours == "Mo-Fr 08:00-18:00"
    assert cafe.latitude == 37.8723


@pytest.mark.parametrize(
    "element",
    [
        None,
        "a string",
        42,
        {},
        {"tags": {"amenity": "cafe"}},  # no name
        {"tags": {"name": "Somewhere"}},  # no usable kind
        {"tags": {"name": "X", "amenity": "cafe"}},  # no coordinates
        {"lat": 1, "lon": 2, "tags": {"name": "X", "amenity": "parking"}},  # not a kind we use
        {"lat": 999, "lon": 2, "tags": {"name": "X", "amenity": "cafe"}},  # off the planet
    ],
)
def test_unusable_osm_elements_are_skipped(element: object) -> None:
    assert campus.parse_spot(element) is None


def test_a_building_mapped_twice_appears_once() -> None:
    assert len(campus.parse_campus([OSM_CAFE, dict(OSM_CAFE)])) == 1


def test_bad_elements_do_not_lose_the_good_ones() -> None:
    spots = campus.parse_campus([OSM_DORM, None, "junk", {}, OSM_CAFE])
    assert len(spots) == 2


async def test_a_campus_with_nothing_mapped_is_reported(db: Store) -> None:
    async def nothing(name: str, latitude: float, longitude: float):
        return []

    with pytest.raises(campus.CampusError, match="doesn't have this campus mapped"):
        await campus.load("x.edu", "X", 1.0, 2.0, nothing)
    assert await campus.load_quietly("x.edu", "X", 1.0, 2.0, nothing) == []


async def test_a_map_failure_does_not_crash_the_warm_up(db: Store) -> None:
    async def broken(name: str, latitude: float, longitude: float):
        raise RuntimeError("boom")

    assert await campus.load_quietly("x.edu", "X", 1.0, 2.0, broken) == []


async def test_a_loaded_campus_comes_back_as_spots() -> None:
    async def two(name: str, latitude: float, longitude: float):
        return [OSM_DORM, OSM_CAFE]

    spots = await campus.load("berkeley.edu", "Berkeley", 37.87, -122.26, two)
    assert len(spots) == 2


# --- Finding a campus from just an email domain ---------------------------------------------


def test_a_school_key_becomes_something_searchable() -> None:
    assert campus.school_label_for("berkeley.edu") == "Berkeley"
    assert campus.school_label_for("ox.ac.uk") == "Ox"
    assert campus.school_label_for("texas-am.edu") == "Texas Am"


async def test_a_school_is_placed_then_its_campus_fetched() -> None:
    async def geocode(school: str):
        return "University of California, Berkeley", 37.8719, -122.2585

    async def fetch(name: str, latitude: float, longitude: float):
        assert name == "University of California, Berkeley"
        return [OSM_DORM, OSM_CAFE]

    spots = await campus.find_and_load("berkeley.edu", geocode, fetch)
    assert {s.name for s in spots} == {"Unit 1 Residence Hall", "Free Speech Movement Cafe"}


async def test_a_school_that_cannot_be_placed_has_no_campus() -> None:
    async def nowhere(school: str):
        return None

    async def unused(name: str, latitude: float, longitude: float):
        raise AssertionError("should not be fetched for an unplaceable school")

    assert await campus.find_and_load("nowhere.edu", nowhere, unused) == []


async def test_a_geocode_failure_is_not_fatal() -> None:
    async def broken(school: str):
        raise campus.CampusError("the place service is down")

    async def unused(name: str, latitude: float, longitude: float):
        raise AssertionError("should not be fetched")

    assert await campus.find_and_load("berkeley.edu", broken, unused) == []


async def test_warming_a_campus_caches_it_once(db: Store) -> None:
    calls = [0]

    async def find(school: str):
        calls[0] += 1
        return [CAFE, LIBRARY]

    assert await students.warm_campus(db, "stanford.edu", find) == 2
    assert await students.warm_campus(db, "stanford.edu", find) == 2
    assert calls[0] == 1  # the second call reads the store, not the map service


async def test_warming_an_unmappable_campus_does_not_cache_emptiness(db: Store) -> None:
    async def nothing(school: str):
        return []

    assert await students.warm_campus(db, "nowhere.edu", nothing) == 0
    assert not db.campus_loaded("nowhere.edu")  # so it can be retried later


async def test_only_schools_with_a_verified_student_are_warmed(db: Store) -> None:
    asked: list[str] = []

    async def find(school: str):
        asked.append(school)
        return [CAFE]

    enrol(db, ALICE, "alice@berkeley.edu")  # berkeley is already cached by the fixture
    enrol(db, CARL, "carl@stanford.edu")
    students.begin(db, BOB, "bob@mit.edu")  # never confirmed
    await students.warm_known_campuses(db, find)
    assert asked == ["stanford.edu"]
