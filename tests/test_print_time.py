from core.print_time import (estimate_plan, detect_model, fmt_duration, laser_job_seconds,
                             plotter_job_seconds, merged_profiles)


def test_detect_models():
    assert detect_model("laser", "HP LaserJet Pro MFP M435 PCL 6") == "M435"
    assert detect_model("laser", "HP MFP M436 PCL6") == "M436"
    assert detect_model("laser", "Microsoft Print to PDF") == "default"
    assert detect_model("plotter", "Canon TM-305") == "TM-305"
    assert detect_model("plotter", "Canon TM-350") == "TM-305"


def test_laser_speed_depends_on_model_and_format():
    p = merged_profiles()["laser"]
    m435_a4 = laser_job_seconds(100, "A4", 1, p["M435"], 8)
    m436_a4 = laser_job_seconds(100, "A4", 1, p["M436"], 8)
    m435_a3 = laser_job_seconds(100, "A3", 1, p["M435"], 8)
    assert m436_a4 > m435_a4          # M436 медленнее (23 против 31 стр/мин)
    assert m435_a3 > m435_a4          # A3 медленнее A4
    assert abs(laser_job_seconds(100, "A4", 3, p["M435"], 8) - 3 * m435_a4) < 1e-6  # копии


def test_plotter_by_area():
    p = merged_profiles()["plotter"]["TM-305"]
    a0 = plotter_job_seconds([(841, 1189)], 1, p, 0)
    a1 = plotter_job_seconds([(594, 841)], 1, p, 0)
    assert abs(a0 - (74 + 8)) < 0.5 and a1 < a0


def test_estimate_plan_parallel_and_copies():
    plan = {
        "laser": [("a.pdf", "a.pdf", [1, 2, 3], {"format": "A4", "missing": 2})],
        "plotter": [("a.pdf", "a.pdf", [4], {"format": "A1", "missing": 2})],
        "stats": {},
    }
    sizes = {("a.pdf", 4): (594.0, 841.0)}
    e = estimate_plan(plan, sizes, "HP MFP M436 PCL6", "Canon TM-305")
    assert e["laser"] > 0 and e["plotter"] > 0
    assert e["wall"] == max(e["laser"], e["plotter"])
    assert abs(e["per_file"]["a.pdf"] - e["sum"]) < 1e-6
    assert e["laser_model"].startswith("HP LaserJet MFP M436")


def test_user_overrides():
    plan = {"laser": [("a.pdf", "a.pdf", list(range(1, 61)), {"format": "A4", "missing": 1})], "plotter": []}
    slow = estimate_plan(plan, {}, "M435", "", {"laser": {"M435": {"A4_ppm": 10}}})
    fast = estimate_plan(plan, {}, "M435", "")
    assert slow["laser"] > fast["laser"]


def test_fmt_duration():
    assert fmt_duration(0) == "0 мин"
    assert fmt_duration(30) == "<1 мин"
    assert fmt_duration(61) == "2 мин"
    assert fmt_duration(3900) == "1 ч 05 мин"
