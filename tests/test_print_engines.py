import pytest
from unittest.mock import patch
from printing.sumatra_engine import SumatraEngine
from printing.foxit_engine import FoxitEngine

@patch("subprocess.run")
@patch("os.path.exists", return_value=True)
def test_sumatra_engine_cmd(mock_exists, mock_run):
    engine = SumatraEngine("SumatraPDF.exe")
    engine.print_file("test.pdf", "MyPrinter", "fit,paper=A1")
    
    cmd = mock_run.call_args[0][0]
    assert cmd == [
        "SumatraPDF.exe",
        "-print-to", "MyPrinter",
        "-print-settings", "fit,paper=A1",
        "-silent",
        "test.pdf"
    ]

@patch("subprocess.run")
@patch("os.path.exists", return_value=True)
def test_foxit_engine_cmd(mock_exists, mock_run):
    engine = FoxitEngine("FoxitPDFEditor.exe")
    engine.print_file("test.pdf", "MyPrinter", "fit")
    
    cmd = mock_run.call_args[0][0]
    assert cmd == [
        "FoxitPDFEditor.exe",
        "/t",
        "test.pdf",
        "MyPrinter"
    ]
