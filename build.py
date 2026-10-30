import PyInstaller.__main__
import os
import sys

if __name__ == '__main__':
    # Determine the directory of the script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    sys.path.insert(0, script_dir)
    from core.version import APP_VERSION
    
    # В GitHub Actions exe называется просто WPM.exe (имя не меняется при обновлениях),
    # локально — WPM_<версия>.exe
    exe_name = os.environ.get("WPM_EXE_NAME") or f"WPM_{APP_VERSION}"

    args = [
        'main.py',
        "--name", exe_name,
        '--clean',
        '--noconfirm',
        '--onefile',
        '--windowed',
        '--icon=ui/assets/icon.ico',
        '--add-data=ui/assets;ui/assets',
        '--add-data=docs;docs',            # справка «?»
        '--hidden-import=win32timezone',
        '--collect-data=customtkinter',
        # PyMuPDF: шрифты и служебные файлы. Без них новые версии PyMuPDF в собранном exe
        # падают с FileNotFoundError при создании/записи PDF (самопроверка, растеризация).
        '--collect-all=pymupdf',
    ]
    # Только нужные инструменты, без кэша SumatraPDF
    for tool in ("SumatraPDF.exe", "PDFtoPrinter.exe", "SumatraPDF-settings.txt"):
        if os.path.exists(os.path.join("tools", tool)):
            args.append(f"--add-data=tools/{tool};tools")
    PyInstaller.__main__.run(args)

    print("Build complete! Check the 'dist' folder.")
