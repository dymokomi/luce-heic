#!/usr/bin/env python3
"""luce-heic's gate: the module's test blocks in native and C modes, then the corpus
oracle (tests/oracle.lucb) built both ways. LUCE_BASE names the compiler and LUCE_STD its
standard library; by default they are the luce-base checkout beside this package."""
import os, subprocess
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
BASE = Path(os.environ.get("LUCE_BASE") or ROOT.parent / "luce-base/build/luce-base").resolve()
STD = Path(os.environ.get("LUCE_STD") or ROOT.parent / "luce-base/src/std").resolve()
env = dict(os.environ, LUCE_BASE=str(BASE), LUCE_STD=str(STD))
(ROOT / "build").mkdir(exist_ok=True)
for flags in [["--native"], ["--backend=c"]]:
    subprocess.run([str(BASE), "test", str(ROOT / "src/luce_heic/heic"), *flags], env=env, check=True, timeout=600, cwd=ROOT)
for name, flags in [("oracle", ["--native"]), ("oracle_c", ["--backend=c", "--release"])]:
    subprocess.run([str(BASE), "build", "oracle.lucb", *flags, "-o", str(ROOT / "build" / name)], env=env, check=True, timeout=600, cwd=ROOT / "tests")
    subprocess.run([str(ROOT / "build" / name), str(ROOT / "tests/corpus")], check=True, timeout=900)
print("PASS luce-heic")
