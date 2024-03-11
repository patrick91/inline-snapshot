from inline_snapshot import snapshot


def test_fix_list_fix(check_update):
    assert check_update(
        """assert [1,2]==snapshot([0+1,3])""", reported_flags="update,fix", flags="fix"
    ) == snapshot("""assert [1,2]==snapshot([0+1,2])""")


def test_fix_list_insert(check_update):
    assert check_update(
        """assert [1,2,3,4,5,6]==snapshot([0+1,3])""",
        reported_flags="update,fix",
        flags="fix",
    ) == snapshot("assert [1,2,3,4,5,6]==snapshot([0+1,2, 3, 4, 5, 6])")


def test_fix_list_delete(check_update):
    assert check_update(
        """assert [1,5]==snapshot([0+1,2,3,4,5])""",
        reported_flags="update,fix",
        flags="fix",
    ) == snapshot("assert [1,5]==snapshot([0+1,5])")


def test_fix_dict_change(check_update):
    assert check_update(
        """assert {1:1, 2:2}==snapshot({1:0+1, 2:3})""",
        reported_flags="update,fix",
        flags="fix",
    ) == snapshot("""assert {1:1, 2:2}==snapshot({1:0+1, 2:2})""")


def test_fix_dict_remove(check_update):
    assert check_update(
        """assert {1:1}==snapshot({0:0, 1:0+1, 2:2})""",
        reported_flags="update,fix",
        flags="fix",
    ) == snapshot("assert {1:1}==snapshot({ 1:0+1, })")

    assert check_update(
        """assert {}==snapshot({0:0})""",
        reported_flags="fix",
        flags="fix",
    ) == snapshot("assert {}==snapshot({})")


def test_fix_dict_insert(check_update):
    assert check_update(
        """assert {0:"before",1:1,2:"after"}==snapshot({1:0+1})""",
        reported_flags="update,fix",
        flags="fix",
    ) == snapshot(
        """assert {0:"before",1:1,2:"after"}==snapshot({0:"before", 1:0+1, 2:"after"})"""
    )


def test_fix_dict_with_non_literal_keys(check_update):
    assert check_update(
        """assert {1+2:"3"}==snapshot({1+2:"5"})""",
        reported_flags="update,fix",
        flags="fix",
    ) == snapshot('assert {1+2:"3"}==snapshot({1+2:"3"})')
