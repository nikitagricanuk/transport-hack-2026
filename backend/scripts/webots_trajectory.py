"""Webots Supervisor controller for exporting vehicle poses to the editor."""

import argparse
import csv
import math
from pathlib import Path


def main():
    from controller import Supervisor
    from pyproj import Transformer

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vehicles", nargs="+", help="Vehicle DEF names")
    parser.add_argument("--epsg", type=int, required=True, help="Projected CRS used by the world")
    parser.add_argument("--east", type=float, required=True, help="Projected easting at world X=0")
    parser.add_argument("--north", type=float, required=True, help="Projected northing at world Y=0")
    parser.add_argument("--axis", choices=["x", "y", "-x", "-y"], default="x")
    parser.add_argument("--duration", type=float, default=90)
    parser.add_argument("--output", type=Path, default=Path("webots-trajectories.csv"))
    args = parser.parse_args()

    robot = Supervisor()
    nodes = {name: robot.getFromDef(name) for name in args.vehicles}
    missing = [name for name, node in nodes.items() if node is None]
    if missing:
        parser.error(f"Vehicle DEF not found: {', '.join(missing)}")
    project = Transformer.from_crs(args.epsg, 4326, always_xy=True)
    axis = 0 if args.axis.endswith("x") else 1
    sign = -1 if args.axis.startswith("-") else 1
    step = int(robot.getBasicTimeStep())
    start, next_sample = robot.getTime(), 0.0
    with args.output.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["time", "id", "lon", "lat", "angle"])
        while robot.step(step) != -1:
            time = robot.getTime() - start
            if time > args.duration:
                break
            if time < next_sample:
                continue
            next_sample += 0.25
            for name, node in nodes.items():
                x, y, _ = node.getPosition()
                rotation = node.getOrientation()
                east, north = args.east + x, args.north + y
                lon, lat = project.transform(east, north)
                ahead_lon, ahead_lat = project.transform(east + sign * rotation[axis],
                                                          north + sign * rotation[axis + 3])
                angle = math.degrees(math.atan2((ahead_lon - lon) * math.cos(math.radians(lat)),
                                                ahead_lat - lat)) % 360
                writer.writerow([f"{time:.3f}", name, f"{lon:.8f}", f"{lat:.8f}", f"{angle:.2f}"])


if __name__ == "__main__":
    main()
