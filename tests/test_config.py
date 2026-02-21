from classifieds_hub.core.config import Settings


def test_parse_target_cities_csv() -> None:
    settings = Settings(TARGET_CITIES="Aykhal,Udachny")
    assert settings.TARGET_CITIES == ["Aykhal", "Udachny"]


def test_parse_run_hours_csv() -> None:
    settings = Settings(RUN_HOURS_LOCAL="09:00,14:00,18:00,22:00")
    assert settings.RUN_HOURS_LOCAL == ["09:00", "14:00", "18:00", "22:00"]
