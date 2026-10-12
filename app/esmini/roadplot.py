"""Draw the lane borders sampled by `odrplot` as a PNG (FR-05, Tier 1: no OpenGL).

Run as a separate process, so that the service can kill it on timeout:

    python -m app.esmini.roadplot <odrplot.csv> <out.png> <width> <height> <road-ids: 0|1> <title>

`odrplot` writes one block per lane border: a line `lane, <roadId>, <laneSection>, <laneId>, <laneType>`, followed
by `x, y, z, heading` rows. Lane 0 is the road's reference line. The colours follow esmini's own viewer script
`EnvironmentSimulator/Applications/odrplot/xodr.py`: reference lines red, driving lanes blue, other lanes grey.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path

REFERENCE_COLOUR = "#BB5555"
DRIVING_COLOUR = "#3333BB"
OTHER_COLOUR = "#AAAAAA"


@dataclass
class Border:
    road: str
    section: str
    lane: int
    lane_type: str
    x: list[float] = field(default_factory=list)
    y: list[float] = field(default_factory=list)
    z: list[float] = field(default_factory=list)


def read_borders(path: Path) -> list[Border]:
    borders: list[Border] = []
    with path.open(newline="") as f:
        for row in csv.reader(f, skipinitialspace=True):
            if not row:
                continue
            if row[0] == "lane":
                borders.append(Border(road=row[1], section=row[2], lane=int(row[3]), lane_type=row[4]))
            elif borders:
                borders[-1].x.append(float(row[0]))
                borders[-1].y.append(float(row[1]))
                borders[-1].z.append(float(row[2]))
    return [b for b in borders if b.x]


def plot(borders: list[Border], output: Path, width: int, height: int, road_ids: bool, title: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    dpi = 100
    fig, ax = plt.subplots(figsize=(width / dpi, height / dpi), dpi=dpi)
    for b in borders:  # outer lanes first, reference lines on top
        if b.lane != 0:
            colour = DRIVING_COLOUR if b.lane_type == "driving" else OTHER_COLOUR
            ax.plot(b.x, b.y, color=colour, linewidth=0.6)
    for b in borders:
        if b.lane == 0:
            ax.plot(b.x, b.y, color=REFERENCE_COLOUR, linewidth=1.2)
    if road_ids:
        for b in borders:
            if b.lane == 0 and b.section == "0":
                i = len(b.x) // 3
                ax.annotate(
                    b.road,
                    (b.x[i], b.y[i]),
                    fontsize=7,
                    ha="center",
                    va="center",
                    bbox={"boxstyle": "round,pad=0.15", "fc": "white", "ec": REFERENCE_COLOUR, "lw": 0.5},
                )
    ax.set_aspect("equal", adjustable="datalim")
    ax.set_xlabel("x [m]")
    ax.set_ylabel("y [m]")
    ax.set_title(title, fontsize=9)
    ax.grid(True, linewidth=0.3, alpha=0.5)
    fig.tight_layout()
    fig.savefig(output, format="png")
    plt.close(fig)


def main(argv: list[str]) -> int:
    source, output, width, height, road_ids, title = argv
    borders = read_borders(Path(source))
    if not borders:
        print("odrplot sampled no lanes; the road network is empty", file=sys.stderr)
        return 2
    plot(borders, Path(output), int(width), int(height), road_ids == "1", title)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
