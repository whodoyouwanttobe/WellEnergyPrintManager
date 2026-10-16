"""Вызывается в GitHub Actions перед сборкой.

Определяет канал по ветке (main -> stable, beta -> beta, dev -> dev),
прописывает BUILD_CHANNEL/BUILD_NUMBER в core/version.py и печатает тег релиза
в $GITHUB_OUTPUT (tag=..., channel=..., prerelease=true/false).
"""
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VERSION_FILE = os.path.join(ROOT, "core", "version.py")
BRANCH_TO_CHANNEL = {"main": "stable", "master": "stable", "beta": "beta", "dev": "dev"}


def main():
    branch = os.environ.get("GITHUB_REF_NAME", "")
    build = int(os.environ.get("GITHUB_RUN_NUMBER", "0"))
    channel = BRANCH_TO_CHANNEL.get(branch)
    if channel is None:
        print(f"Ветка {branch!r} не публикует релизы", file=sys.stderr)
        channel = "dev"

    src = open(VERSION_FILE, encoding="utf-8").read()
    m = re.search(r'^APP_VERSION\s*=\s*"(\d+\.\d+\.\d+)"', src, re.M)
    if not m:
        sys.exit("APP_VERSION в core/version.py должен быть вида \"4.9.0\"")
    version = m.group(1)
    src = re.sub(r'^BUILD_CHANNEL\s*=.*$', f'BUILD_CHANNEL = "{channel}"', src, flags=re.M)
    src = re.sub(r'^BUILD_NUMBER\s*=.*$', f'BUILD_NUMBER = {build}', src, flags=re.M)
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        f.write(src)

    tag = f"v{version}" if channel == "stable" else f"v{version}-{channel}.{build}"
    out = os.environ.get("GITHUB_OUTPUT")
    lines = [f"tag={tag}", f"channel={channel}", f"version={version}",
             f"prerelease={'false' if channel == 'stable' else 'true'}"]
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
