"""Packets captured from APRS-IS; expected values cross-checked with aprslib."""
import pytest

from link.aprs_packet import category, parse_packet


def near(value, expected, tolerance=1e-4):
    return abs(value - expected) <= tolerance


def test_uncompressed_with_course_speed_and_altitude():
    p = parse_packet(
        "N1FLO-14>APDR16,TCPIP*,qAC,T2FINLAND:=4125.94N/07248.30W$050/045/A=000125 https://aprsdroid.org/"
    )
    assert p.name == "N1FLO-14" and p.kind == "station"
    assert near(p.lat, 41.432333) and near(p.lon, -72.805)
    assert (p.symbol_table, p.symbol_code) == ("/", "$")
    assert p.course == 50 and near(p.speed_kmh, 83.3, 0.1) and near(p.altitude_m, 38.1, 0.1)
    assert p.comment == "https://aprsdroid.org/"


def test_timestamped_position():
    p = parse_packet("AJ1L>APAGW,WIDE2-1,qAR,W1MV-1:@281410z4205.64N/07054.25W-Rockland, MA")
    assert near(p.lat, 42.094) and near(p.lon, -70.904167)
    assert p.comment == "Rockland, MA"


def test_compressed_with_course_speed_and_telemetry_stripped():
    p = parse_packet("M1NER-8>APLRFT,WIDE1-1,WIDE2-1,qAR,G7DCD-10:!/4<=CN0ui[OVGM1NERs LoRa APRS Tracker|&Z%S!!|")
    assert near(p.lat, 51.819309) and near(p.lon, -1.263836)
    assert p.symbol_code == "[" and p.course == 184 and near(p.speed_kmh, 107.6, 0.1)
    assert p.comment == "M1NERs LoRa APRS Tracker"


def test_compressed_altitude():
    p = parse_packet("G4FBA-11>APLRT1,WIDE1-1,qAR,M0TLJ-10:=/3;v3N6/`>=VQ")
    assert near(p.lat, 53.805734) and near(p.lon, -1.036459)
    assert near(p.altitude_m, 55.1, 0.1) and p.course is None


def test_compressed_overlay_letter_becomes_digit():
    p = parse_packet("KC2YRE-2>APDW18,WIDE2-1,qAR,W2AEE:!b9qFM;kN;#  ! Digipeater WIDE1 DigiPi")
    assert (p.symbol_table, p.symbol_code) == ("1", "#")
    assert category(p) == "digipeater"


def test_mic_e_with_ambiguity_altitude_and_radio_marker():
    p = parse_packet("KB6Q-9>S8SSPZ,WIDE1-1,WIDE2-1,qAR,KA6PRW:`1/Nl#+k/`\"43}146.520MHz 146.520_%")
    assert near(p.lat, 38.550833) and near(p.lon, -121.325833)
    assert (p.symbol_table, p.symbol_code) == ("/", "k")
    assert p.altitude_m == 28.0 and p.speed_kmh == 0.0
    assert p.comment == "146.520MHz 146.520"
    assert category(p) == "mobile"


def test_mic_e_kenwood_moving():
    p = parse_packet("W6ILO-9>S7TQTZ,WIDE1-1,WIDE2-1,qAR,WA6ODP-2:`1Qvn*wk/`\"4p}_5")
    assert near(p.lat, 37.690833) and near(p.lon, -121.899167)


def test_ambiguous_uncompressed_position_is_centered():
    p = parse_packet("M1RKY-2>APGRWO,TCPIP*,qAC,T2PANAMA:!5257.2 N/00159.1 W-/A=000440/144.800MHz, RPi4")
    assert near(p.lat, 52.954167) and near(p.lon, -1.985833)
    assert p.comment == "/144.800MHz, RPi4"


def test_repeater_object():
    p = parse_packet("N3TJJ-12>APMI06,TCPIP*,qAS,N3TJJ:;448.225PG*111111z4030.41N/07622.60Wr448.225MHz C082 -500 AARG")
    assert p.name == "448.225PG" and p.source == "N3TJJ-12" and p.kind == "object"
    assert near(p.lat, 40.506833) and near(p.lon, -76.376667)
    assert category(p) == "repeater" and not p.killed


def test_killed_object_and_item():
    killed = parse_packet("N3TJJ-12>APMI06,TCPIP*:;448.225PG_111111z4030.41N/07622.60Wr")
    assert killed.killed and killed.name == "448.225PG"
    item = parse_packet("W4VA-13>APMI06,TCPIP*,qAS,KX4O:)W21_3844.04N\\07750.01W_")
    assert item.kind == "item" and item.name == "W21" and item.killed


def test_weather_report():
    p = parse_packet(
        "AJ1L>APAGW,WIDE2-1,qAR,W1MV-1:@281410z4205.64N/07054.25W_079/002g005t060r001p065P021h97b10095 Rockland WX"
    )
    assert category(p) == "weather"
    assert p.weather == {
        "wind_dir": 79, "wind_mph": 2, "wind_gust_mph": 5, "temp_f": 60, "rain_1h_in": 0.01,
        "rain_24h_in": 0.65, "rain_midnight_in": 0.21, "humidity": 97, "pressure_mbar": 1009.5,
    }
    assert p.comment == "Rockland WX"
    assert p.course is None


def test_weather_with_c_s_wind_fields_and_blanks():
    p = parse_packet("DC6RD>APRS,TCPIP*,qAC,T2NUERNBG:=4926.01N/01151.09E_c088s006g010t065r000h59b10220_RD")
    assert p.weather["wind_dir"] == 88 and p.weather["temp_f"] == 65 and p.weather["humidity"] == 59
    p = parse_packet("DL3APY-3>APRS,qAO,DL3APY-10:!5103.46N/01114.66E_180/00 g...t072r000p...P...h49b10342 WX")
    assert p.weather["wind_dir"] == 180 and p.weather["wind_mph"] == 0 and p.weather["temp_f"] == 72
    assert "wind_gust_mph" not in p.weather


def test_third_party_is_unwrapped():
    p = parse_packet("W1XYZ>APRS,TCPIP*:}K1ABC-7>APRS,TCPIP,W1XYZ*:!4205.64N/07054.25W>")
    assert p.name == "K1ABC-7" and category(p) == "mobile"


@pytest.mark.parametrize(
    "line",
    [
        "# aprsc 2.1.19-g730c5c0",
        "K1ABC>APRS::W1AW     :hello{01",  # message
        "K1ABC>APRS:>On the air",  # status
        "K1ABC>APRS:T#005,199,000,255,073,123,01101001",  # telemetry
        "PA0RNH-10>APSN01,TCPIP*,qAC,T2CAEAST:=5246.14N#00505.73E2CSN iGate",  # invalid symbol table
        "K1ABC>APRS:!0000.00N/00000.00W>no fix",
        "garbage",
        "K1ABC>APRS:!49",
    ],
)
def test_ignored_or_malformed(line):
    assert parse_packet(line) is None


def test_software_tags_are_stripped_from_comments():
    p = parse_packet("KB7KFC-3>APRS:=3310.00N/11150.00W_/South Chandler weather {UIV32N}")
    assert p is not None and p.comment == "/South Chandler weather"
    p = parse_packet("SOMTNX>APLRG1:!3320.00N/11205.00W#LoRa Digi de N7UV{T36M}")
    assert p is not None and p.comment == "LoRa Digi de N7UV"


def test_weather_symbol_without_weather_still_reads_altitude():
    p = parse_packet("K7PMV-Y>APDG03:!3310.00N\\11150.00W_/A=00000070cm MMDVM Voice (C4FM) 441.00000MHz")
    assert p is not None and p.altitude_m == 0 and p.comment == "70cm MMDVM Voice (C4FM) 441.00000MHz"
