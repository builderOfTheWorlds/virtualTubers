import threading
import time

import pytest

from avatar.state_machine import AvatarState, AvatarStateMachine


class TestAvatarState:
    def test_states_exist(self):
        assert AvatarState.IDLE is not None
        assert AvatarState.THINKING is not None
        assert AvatarState.SPEAKING is not None
        assert AvatarState.LISTENING is not None
        assert AvatarState.ERROR is not None


class TestStateMachine:
    def test_initial_state_is_idle(self):
        sm = AvatarStateMachine()
        assert sm.state == AvatarState.IDLE

    def test_transition_to_thinking(self):
        sm = AvatarStateMachine()
        sm.transition(AvatarState.THINKING)
        assert sm.state == AvatarState.THINKING

    def test_transition_to_speaking(self):
        sm = AvatarStateMachine()
        sm.transition(AvatarState.SPEAKING)
        assert sm.state == AvatarState.SPEAKING

    def test_transition_to_listening(self):
        sm = AvatarStateMachine()
        sm.transition(AvatarState.LISTENING)
        assert sm.state == AvatarState.LISTENING

    def test_transition_to_error(self):
        sm = AvatarStateMachine()
        sm.transition(AvatarState.ERROR)
        assert sm.state == AvatarState.ERROR

    def test_transition_back_to_idle(self):
        sm = AvatarStateMachine()
        sm.transition(AvatarState.THINKING)
        sm.transition(AvatarState.IDLE)
        assert sm.state == AvatarState.IDLE

    def test_entry_exit_callbacks(self):
        log = []
        sm = AvatarStateMachine(
            on_enter=lambda s: log.append(("enter", s)),
            on_exit=lambda s: log.append(("exit", s)),
        )
        sm.transition(AvatarState.THINKING)
        assert log == [("exit", AvatarState.IDLE), ("enter", AvatarState.THINKING)]

    def test_no_callback_on_same_state(self):
        log = []
        sm = AvatarStateMachine(
            on_enter=lambda s: log.append(("enter", s)),
            on_exit=lambda s: log.append(("exit", s)),
        )
        sm.transition(AvatarState.IDLE)
        assert log == []

    def test_thread_safety(self):
        sm = AvatarStateMachine()
        results = []

        def rapid_transitions(target_state, count):
            for _ in range(count):
                sm.transition(target_state)
                results.append(sm.state)

        t1 = threading.Thread(target=rapid_transitions, args=(AvatarState.THINKING, 100))
        t2 = threading.Thread(target=rapid_transitions, args=(AvatarState.SPEAKING, 100))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # All results should be valid states (no corruption)
        for r in results:
            assert r in AvatarState

    @pytest.mark.timeout(5)
    def test_auto_idle_timeout(self):
        sm = AvatarStateMachine(idle_timeout=0.3)
        sm.transition(AvatarState.THINKING)
        assert sm.state == AvatarState.THINKING
        time.sleep(0.5)
        assert sm.state == AvatarState.IDLE

    @pytest.mark.timeout(5)
    def test_auto_idle_resets_on_new_transition(self):
        sm = AvatarStateMachine(idle_timeout=0.5)
        sm.transition(AvatarState.THINKING)
        time.sleep(0.2)
        sm.transition(AvatarState.SPEAKING)
        time.sleep(0.2)
        # Should still be speaking — timeout reset when we transitioned
        assert sm.state == AvatarState.SPEAKING

    def test_speaking_with_phoneme_data(self):
        sm = AvatarStateMachine()
        phonemes = [
            {"phoneme": "h", "start": 0.0, "end": 0.1},
            {"phoneme": "ɛ", "start": 0.1, "end": 0.2},
        ]
        sm.transition(AvatarState.SPEAKING, phoneme_data=phonemes)
        assert sm.state == AvatarState.SPEAKING
        assert sm.phoneme_data == phonemes

    def test_phoneme_data_cleared_on_exit_speaking(self):
        sm = AvatarStateMachine()
        sm.transition(AvatarState.SPEAKING, phoneme_data=[{"phoneme": "a"}])
        sm.transition(AvatarState.IDLE)
        assert sm.phoneme_data == []

    def test_shutdown(self):
        sm = AvatarStateMachine(idle_timeout=1.0)
        sm.transition(AvatarState.THINKING)
        sm.shutdown()
        # After shutdown, idle timer thread should be stopped
        assert sm._shutdown_event.is_set()
