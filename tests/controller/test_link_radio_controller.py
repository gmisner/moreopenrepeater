from controller.link_radio import HANG_SECONDS, LinkPlay, LinkPTT, LinkRadioController, LinkRadioSettings


def run(controller, start, end, sending, receiving=False, step=0.05):
    commands = []
    now = start
    while now < end - 1e-9:
        commands += controller.update(now, sending, receiving)
        now = round(now + step, 6)
    return commands


def test_keys_while_the_repeater_repeats_a_local_user_then_courtesy_tone_and_hang():
    c = LinkRadioController(LinkRadioSettings(courtesy_tone=True))

    assert c.update(0.0, sending=True, receiving=False) == [LinkPTT(True)]
    assert run(c, 0.05, 5.0, sending=True) == []
    assert c.update(5.0, sending=False, receiving=False) == [LinkPlay("courtesy_tone")]
    assert c.ptt
    assert run(c, 5.05, 5.0 + HANG_SECONDS, sending=False) == []
    assert c.update(5.0 + HANG_SECONDS, sending=False, receiving=False) == [LinkPTT(False)]


def test_no_courtesy_tone_when_turned_off():
    c = LinkRadioController(LinkRadioSettings(courtesy_tone=False))
    c.update(0.0, sending=True, receiving=False)

    commands = run(c, 1.0, 2.0, sending=False)

    assert commands == [LinkPTT(False)]


def test_a_user_who_keys_again_during_the_hang_keeps_the_link_up():
    c = LinkRadioController(LinkRadioSettings(courtesy_tone=False))
    c.update(0.0, sending=True, receiving=False)
    c.update(1.0, sending=False, receiving=False)

    assert c.update(1.2, sending=True, receiving=False) == []
    assert run(c, 1.25, 3.0, sending=True) == []
    assert c.ptt


def test_never_transmits_over_the_far_end():
    c = LinkRadioController()

    assert run(c, 0.0, 2.0, sending=True, receiving=True) == []
    assert not c.ptt


def test_far_end_keying_up_unkeys_the_link_at_once_without_a_courtesy_tone():
    c = LinkRadioController(LinkRadioSettings(courtesy_tone=True))
    c.update(0.0, sending=True, receiving=False)

    assert c.update(1.0, sending=True, receiving=True) == [LinkPTT(False)]


def test_timeout_drops_the_link_until_the_user_unkeys():
    c = LinkRadioController(LinkRadioSettings(timeout=60.0, courtesy_tone=False))
    c.update(0.0, sending=True, receiving=False)

    commands = run(c, 0.05, 61.0, sending=True)

    assert commands == [LinkPlay("timeout_tone"), LinkPTT(False)]
    assert c.timed_out
    assert run(c, 61.0, 120.0, sending=True) == []  # still talking: stays off
    c.update(120.0, sending=False, receiving=False)
    assert not c.timed_out
    assert c.update(121.0, sending=True, receiving=False) == [LinkPTT(True)]


def test_ids_within_the_interval_of_its_first_transmission_even_once_idle():
    c = LinkRadioController(LinkRadioSettings(courtesy_tone=False, id_interval=600.0))
    c.update(0.0, sending=True, receiving=False)
    c.update(10.0, sending=False, receiving=False)
    run(c, 10.05, 11.0, sending=False)
    assert c.id_owed

    assert run(c, 11.0, 600.0, sending=False, step=1.0) == []
    assert c.update(600.0, sending=False, receiving=False) == [LinkPlay("id")]
    assert not c.id_owed
    assert run(c, 601.0, 2000.0, sending=False, step=1.0) == []  # nothing transmitted since


def test_ids_every_interval_during_a_long_conversation():
    c = LinkRadioController(LinkRadioSettings(timeout=10_000.0, id_interval=600.0))
    c.update(0.0, sending=True, receiving=False)

    commands = run(c, 1.0, 1300.0, sending=True, step=1.0)

    assert commands == [LinkPlay("id"), LinkPlay("id")]


def test_id_waits_for_the_far_end_to_finish():
    c = LinkRadioController(LinkRadioSettings(courtesy_tone=False, id_interval=600.0))
    c.update(0.0, sending=True, receiving=False)
    run(c, 1.0, 2.0, sending=False)

    assert run(c, 600.0, 650.0, sending=False, receiving=True, step=1.0) == []
    assert c.update(650.0, sending=False, receiving=False) == [LinkPlay("id")]


def test_reset_forgets_everything():
    c = LinkRadioController()
    c.update(0.0, sending=True, receiving=False)

    c.reset()

    assert not c.ptt and not c.id_owed
    assert run(c, 1.0, 2000.0, sending=False, step=10.0) == []
