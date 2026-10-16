import pytest
from core.pdf_utils import detect_format

def test_detect_format_a4():
    fmt = detect_format(210.0, 297.0)
    assert fmt == "A4"

def test_detect_format_a3():
    fmt = detect_format(420.0, 297.0)
    assert fmt == "A3"

def test_detect_format_a1():
    fmt = detect_format(841.0, 594.0)
    assert fmt == "A1"

def test_detect_format_custom():
    fmt = detect_format(1000.0, 1000.0)
    assert fmt == "1000x1000"
