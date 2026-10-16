from core import updater


def rel(tag, draft=False):
    return {"tag_name": tag, "draft": draft, "assets": [{"name": "WPM.exe"}]}


def test_parse_tag():
    assert updater.parse_tag("v4.9.0") == ((4, 9, 0), "stable", 0)
    assert updater.parse_tag("v4.10") == ((4, 10, 0), "stable", 0)
    assert updater.parse_tag("v4.9.1-beta.12") == ((4, 9, 1), "beta", 12)
    assert updater.parse_tag("v5.0.0-dev.3") == ((5, 0, 0), "dev", 3)
    assert updater.parse_tag("latest") is None


def test_order_numeric_not_string():
    k = lambda t: updater.sort_key(updater.parse_tag(t))
    assert k("v4.10.0") > k("v4.9.0")
    assert k("v4.9.0") > k("v4.9.0-beta.7") > k("v4.9.0-dev.99")
    assert k("v4.9.0-beta.10") > k("v4.9.0-beta.9")


def test_channels():
    cur = ((4, 9, 0), "stable", 0)
    rels = [rel("v4.9.1-dev.5"), rel("v4.9.1-beta.2"), rel("v4.9.0"), rel("v5.0.0", draft=True)]
    assert updater.pick_update(rels, "stable", cur) is None
    assert updater.pick_update(rels, "beta", cur)["tag_name"] == "v4.9.1-beta.2"
    assert updater.pick_update(rels + [rel("v4.9.1-dev.9")], "dev", cur)["tag_name"] == "v4.9.1-beta.2"
    assert updater.pick_update([rel("v4.9.2-dev.1")], "dev", cur)["tag_name"] == "v4.9.2-dev.1"


def test_stable_release_supersedes_beta():
    cur = ((4, 9, 1), "beta", 4)
    assert updater.pick_update([rel("v4.9.1")], "beta", cur)["tag_name"] == "v4.9.1"
    assert updater.pick_update([rel("v4.9.1-beta.3")], "beta", cur) is None
