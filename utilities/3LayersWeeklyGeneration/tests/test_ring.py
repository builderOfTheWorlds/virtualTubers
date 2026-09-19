"""Tests for src/ring.py — the plot_0 ring-composition math (MVP scope)."""
from fractions import Fraction

import pytest

import ring


class TestChildPolarity:
    def test_descent_preserves_parent_polarity(self):
        assert ring.child_polarity(1, "descent") == 1
        assert ring.child_polarity(-1, "descent") == -1

    def test_keystone_and_ascent_invert(self):
        assert ring.child_polarity(1, "keystone") == -1
        assert ring.child_polarity(1, "ascent") == -1
        assert ring.child_polarity(-1, "keystone") == 1
        assert ring.child_polarity(-1, "ascent") == 1


class TestTraversal:
    def test_forward(self):
        assert ring.traversal(1) == ("descent", "keystone", "ascent")

    def test_reflected(self):
        assert ring.traversal(-1) == ("ascent", "keystone", "descent")


class TestMirrorPartners:
    @pytest.mark.parametrize("d,a", [(d, a) for d in range(1, 13) for a in range(1, 13)])
    def test_symmetric_and_total(self, d, a):
        partners = ring.mirror_partners(d, a)
        reverse = {j: [] for j in range(a)}
        for i, js in partners.items():
            for j in js:
                reverse[j].append(i)
        # total: every descent and every ascent index has >=1 partner
        assert all(len(js) >= 1 for js in partners.values())
        assert all(len(js) >= 1 for js in reverse.values())
        # symmetric: j in M(i) iff i in M(j)
        for i, js in partners.items():
            for j in js:
                assert i in reverse[j]

    def test_reduces_to_classical_rule_when_equal(self):
        for n in range(1, 13):
            partners = ring.mirror_partners(n, n)
            for i in range(n):
                assert partners[i] == [n - 1 - i]

    def test_worked_examples_from_spec(self):
        assert ring.mirror_partners(4, 2) == {0: [1], 1: [1], 2: [0], 3: [0]}
        assert ring.mirror_partners(2, 4) == {0: [2, 3], 1: [0, 1]}
        assert ring.mirror_partners(3, 5) == {0: [3, 4], 1: [1, 2, 3], 2: [0, 1]}

    def test_rejects_nonpositive(self):
        with pytest.raises(ring.RingConfigError):
            ring.mirror_partners(0, 3)
        with pytest.raises(ring.RingConfigError):
            ring.mirror_partners(3, 0)


class TestBuildPlot0Plan:
    def test_sums_and_order_coverage(self):
        plan = ring.build_plot0_plan(28, (16, 4, 8))
        assert len(plan) == 28
        assert [s["order"] for s in plan] == list(range(28))

    def test_rejects_mismatched_sum(self):
        with pytest.raises(ring.RingConfigError):
            ring.build_plot0_plan(28, (16, 4, 9))

    def test_rejects_nonpositive_part(self):
        with pytest.raises(ring.RingConfigError):
            ring.build_plot0_plan(4, (0, 2, 2))

    def test_roles_and_polarity(self):
        plan = ring.build_plot0_plan(10, (4, 2, 4))
        by_order = {s["order"]: s for s in plan}
        for o in range(4):
            assert by_order[o]["role"] == "descent"
            assert by_order[o]["polarity"] == 1
        for o in range(4, 6):
            assert by_order[o]["role"] == "keystone"
            assert by_order[o]["polarity"] == -1
            assert by_order[o]["u_lo"] == "0"
            assert by_order[o]["u_hi"] == "0"
            assert by_order[o]["mirror_of"] == []
        for o in range(6, 10):
            assert by_order[o]["role"] == "ascent"
            assert by_order[o]["polarity"] == -1

    def test_u_intervals_are_exact_fraction_strings(self):
        plan = ring.build_plot0_plan(6, (2, 1, 3))
        by_order = {s["order"]: s for s in plan}
        # descent index 0 (order 0): u in [(2-0-1)/2, (2-0)/2) = [1/2, 1)
        assert by_order[0]["u_lo"] == "1/2"
        assert by_order[0]["u_hi"] == "1"
        # descent index 1 (order 1): u in [0, 1/2)
        assert by_order[1]["u_lo"] == "0"
        assert by_order[1]["u_hi"] == "1/2"
        # ascent index 0 (order 3): u in [0, 1/3)
        assert by_order[3]["u_lo"] == "0"
        assert by_order[3]["u_hi"] == "1/3"

    def test_mirror_of_is_symmetric_across_orders(self):
        plan = ring.build_plot0_plan(10, (4, 2, 4))
        by_order = {s["order"]: s for s in plan}
        for o in range(4):  # descent orders 0-3
            for m in by_order[o]["mirror_of"]:
                assert o in by_order[m]["mirror_of"]
        for o in range(6, 10):  # ascent orders 6-9
            assert by_order[o]["mirror_of"], f"ascent order {o} has no mirror partner"


class TestGenerationPhases:
    def test_phase_split_and_ordering(self):
        plan = ring.build_plot0_plan(10, (4, 2, 4))
        keystone, descent, ascent = ring.generation_phases(plan)
        assert keystone == [4, 5]
        assert descent == [0, 1, 2, 3]
        assert ascent == [6, 7, 8, 9]
