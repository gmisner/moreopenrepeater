"""Standard CTCSS and DTMF tone frequency tables."""

CTCSS_TONES_HZ: tuple[float, ...] = (
    67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5,
    94.8, 97.4, 100.0, 103.5, 107.2, 110.9, 114.8, 118.8, 123.0, 127.3,
    131.8, 136.5, 141.3, 146.2, 151.4, 156.7, 159.8, 162.2, 165.5, 167.9,
    171.3, 173.8, 177.3, 179.9, 183.5, 186.2, 189.9, 192.8, 196.6, 199.5,
    203.5, 206.5, 210.7, 218.1, 225.7, 229.1, 233.6, 241.8, 250.3, 254.1,
)

DTMF_ROW_FREQUENCIES_HZ: tuple[float, ...] = (697.0, 770.0, 852.0, 941.0)
DTMF_COL_FREQUENCIES_HZ: tuple[float, ...] = (1209.0, 1336.0, 1477.0, 1633.0)

DTMF_KEYPAD: tuple[tuple[str, ...], ...] = (
    ("1", "2", "3", "A"),
    ("4", "5", "6", "B"),
    ("7", "8", "9", "C"),
    ("*", "0", "#", "D"),
)

DTMF_FREQUENCIES_BY_DIGIT: dict[str, tuple[float, float]] = {
    DTMF_KEYPAD[row][col]: (DTMF_ROW_FREQUENCIES_HZ[row], DTMF_COL_FREQUENCIES_HZ[col])
    for row in range(4)
    for col in range(4)
}
