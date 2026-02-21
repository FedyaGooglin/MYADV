from classifieds_hub.main import run


def test_main_imports() -> None:
    assert callable(run)
