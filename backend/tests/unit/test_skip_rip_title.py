from app.models.disc_job import TitleState


def test_skipped_state_exists_and_is_distinct():
    assert TitleState.SKIPPED == "skipped"
    assert TitleState.SKIPPED not in (TitleState.COMPLETED, TitleState.FAILED)
