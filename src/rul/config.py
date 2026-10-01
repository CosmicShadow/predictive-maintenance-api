"""Shared constants for the C-MAPSS data and the model."""

OPERATING_SETTINGS = ["op1", "op2", "op3"]
SENSORS = [f"s{i}" for i in range(1, 22)]
MEASUREMENTS = OPERATING_SETTINGS + SENSORS
RAW_COLUMNS = ["unit_id", "cycle", *MEASUREMENTS]

# Early in an engine's life degradation is invisible in the sensors, so the target is capped:
# an engine with 300 cycles left looks the same as one with 125 left.
RUL_CAP = 125
# Number of consecutive cycles the model sees per prediction.
WINDOW = 30
# Columns whose training standard deviation is below this carry no signal and are dropped.
CONSTANT_STD_THRESHOLD = 0.01

DEFAULT_DATASET = "FD001"
