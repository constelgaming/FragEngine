"""Group detected kill timestamps into 20-second edit segments."""

from pathlib import Path

GROUPING_THRESHOLD_SECONDS = 20


def read_timestamps(path: Path) -> list[int]:
    timestamps = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        value = line.strip()
        if not value:
            continue
        try:
            timestamps.append(int(value))
        except ValueError:
            print(f"Skipping invalid timestamp on line {line_number}: {value!r}")
    return timestamps


def group_timestamps(timestamps: list[int]) -> list[list[int]]:
    """Group adjacent detections no more than 20 seconds apart."""
    if not timestamps:
        return []
    numbers = sorted(timestamps)
    groups = []
    current_group = [numbers[0]]
    for number in numbers[1:]:
        if number - current_group[-1] <= GROUPING_THRESHOLD_SECONDS:
            current_group.append(number)
        else:
            groups.append(current_group)
            current_group = [number]
    groups.append(current_group)  # Append the final group exactly once.
    return groups


def main() -> None:
    main_folder = Path(input("Enter main folder: ").strip())
    input_file = main_folder / f"{main_folder.name}_killtime.txt"
    output_file = main_folder / f"{main_folder.name}_grouping.txt"
    if not input_file.is_file():
        raise SystemExit(f"Kill-time file does not exist: {input_file}")
    groups = group_timestamps(read_timestamps(input_file))
    with output_file.open("w", encoding="utf-8") as file:
        for index, group in enumerate(groups):
            print(f"Group-{index}: {group}")
            file.write(f"{group}\n")
    print(f"Wrote {len(groups)} groups to {output_file}")


if __name__ == "__main__":
    main()
