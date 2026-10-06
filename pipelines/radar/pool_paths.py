"""Repository-local radar outputs shared by the pool writers and routine reader."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RADAR = REPO / "out" / "routines" / "radar"
UPSTREAM = RADAR / "upstream"
DATA = RADAR / "data"
