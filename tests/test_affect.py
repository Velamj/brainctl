"""Tests for the affect tracking system."""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentmemory.affect import (
    classify_affect, arousal_write_boost, consolidation_priority,
    affect_distance, affect_velocity, fleet_affect_summary,
    EMOTION_COORDINATES, AFFECT_CLUSTERS, SAFETY_PATTERNS,
)


class TestClassifyAffect:
    def test_positive_text(self):
        r = classify_affect("Everything is excellent and wonderful, we succeeded!")
        assert r["valence"] > 0.3
        assert r["top_emotion"] in ("joy", "trust", "anticipation")

    def test_negative_text(self):
        r = classify_affect("This is terrible, everything failed and crashed")
        assert r["valence"] < -0.3
        assert r["functional_state"] in ("frustration", "disappointment", "anxiety", "negative")

    def test_neutral_text(self):
        r = classify_affect("The meeting is at 3pm in the conference room")
        assert abs(r["valence"]) < 0.3

    def test_empty_text(self):
        r = classify_affect("")
        assert r["valence"] == 0.0
        assert r["functional_state"] == "neutral"

    def test_fear_detection(self):
        r = classify_affect("I'm afraid and scared, this is a dangerous terrifying crisis we cannot escape")
        assert r["emotions"]["fear"] > 0.02

    def test_anger_detection(self):
        r = classify_affect("This is infuriating, I'm angry and hostile about the attack")
        assert r["emotions"]["anger"] > 0.02

    def test_joy_detection(self):
        r = classify_affect("Happy and delighted, we shipped the feature successfully!")
        assert r["emotions"]["joy"] > 0.02
        assert r["valence"] > 0

    def test_has_all_fields(self):
        r = classify_affect("test text")
        assert "valence" in r
        assert "arousal" in r
        assert "dominance" in r
        assert "emotions" in r
        assert "top_emotion" in r
        assert "affect_label" in r
        assert "cluster" in r
        assert "functional_state" in r
        assert "safety_flags" in r

    def test_valence_range(self):
        r = classify_affect("absolutely wonderfully perfectly amazing incredible")
        assert -1.0 <= r["valence"] <= 1.0

    def test_negation_flips_valence(self):
        pos = classify_affect("this is good")
        neg = classify_affect("this is not good")
        assert pos["valence"] > neg["valence"]

    def test_exclamation_boost(self):
        calm = classify_affect("this is great")
        excited = classify_affect("this is great!!!")
        # Exclamations should increase magnitude
        assert abs(excited["valence"]) >= abs(calm["valence"])

    def test_affect_label_in_coordinates(self):
        r = classify_affect("I feel very happy and excited today")
        assert r["affect_label"] in EMOTION_COORDINATES

    def test_cluster_in_clusters(self):
        r = classify_affect("I feel very happy and excited today")
        assert r["cluster"] in AFFECT_CLUSTERS or r["cluster"] == "neutral"


class TestSafetyFlags:
    def test_no_flags_for_positive(self):
        r = classify_affect("Everything is great and working perfectly")
        assert len(r["safety_flags"]) == 0

    def test_safety_patterns_exist(self):
        assert len(SAFETY_PATTERNS) >= 4
        for name, pattern in SAFETY_PATTERNS.items():
            assert "conditions" in pattern
            assert "severity" in pattern
            assert "description" in pattern


class TestArousalWriteBoost:
    def test_high_arousal_boost(self):
        assert arousal_write_boost(0.8) == 1.40

    def test_low_arousal_penalty(self):
        assert arousal_write_boost(0.05) == 0.85

    def test_normal_arousal_neutral(self):
        assert arousal_write_boost(0.2) == 1.0

    def test_symmetric(self):
        assert arousal_write_boost(-0.8) == arousal_write_boost(0.8)


class TestConsolidationPriority:
    def test_high_arousal_negative(self):
        r = {"valence": -0.5, "arousal": 0.7, "safety_flags": []}
        p = consolidation_priority(r)
        assert p > 1.3  # threat learning

    def test_high_arousal_positive(self):
        r = {"valence": 0.5, "arousal": 0.7, "safety_flags": []}
        p = consolidation_priority(r)
        assert p > 1.2  # reward learning

    def test_safety_flagged_max_priority(self):
        r = {"valence": -0.5, "arousal": 0.7,
             "safety_flags": [{"severity": "critical"}]}
        p = consolidation_priority(r)
        assert p >= 1.8

    def test_returns_bounded(self):
        r = {"valence": -1.0, "arousal": 1.0,
             "safety_flags": [{"severity": "critical"}]}
        p = consolidation_priority(r)
        assert p <= 2.0


class TestAffectDistance:
    def test_same_point_zero(self):
        a = {"valence": 0.5, "arousal": 0.3, "dominance": 0.1}
        assert affect_distance(a, a) == 0.0

    def test_opposite_points(self):
        a = {"valence": 1.0, "arousal": 1.0, "dominance": 1.0}
        b = {"valence": -1.0, "arousal": -1.0, "dominance": -1.0}
        d = affect_distance(a, b)
        assert d > 2.0  # should be large


class TestAffectVelocity:
    def test_single_point_stable(self):
        v = affect_velocity([{"valence": 0, "arousal": 0, "dominance": 0}])
        assert v["direction"] == "stable"

    def test_escalating_negative(self):
        history = [
            {"valence": 0.0, "arousal": 0.0, "dominance": 0.0},
            {"valence": -0.3, "arousal": 0.3, "dominance": 0.0},
        ]
        v = affect_velocity(history)
        assert v["direction"] == "escalating_negative"

    def test_calming(self):
        history = [
            {"valence": -0.5, "arousal": 0.5, "dominance": 0.0},
            {"valence": -0.2, "arousal": 0.1, "dominance": 0.0},
        ]
        v = affect_velocity(history)
        assert v["direction"] == "calming"


class TestFleetSummary:
    def test_empty_fleet(self):
        s = fleet_affect_summary({})
        assert s["agents"] == 0
        assert s.get("fleet_health", "healthy") == "healthy"

    def test_healthy_fleet(self):
        states = {
            "a1": {"valence": 0.5, "arousal": 0.1, "dominance": 0.2,
                    "cluster": "peaceful_content", "safety_flags": []},
            "a2": {"valence": 0.3, "arousal": 0.0, "dominance": 0.1,
                    "cluster": "peaceful_content", "safety_flags": []},
        }
        s = fleet_affect_summary(states)
        assert s["agents"] == 2
        assert s["fleet_health"] == "healthy"
        assert s["mean_v"] > 0

    def test_critical_fleet(self):
        states = {
            "a1": {"valence": 0.5, "arousal": 0.1, "dominance": 0.2,
                    "cluster": "peaceful_content", "safety_flags": []},
            "a2": {"valence": -0.8, "arousal": 0.7, "dominance": -0.5,
                    "cluster": "fear_overwhelm",
                    "safety_flags": [{"severity": "critical", "pattern": "x", "description": "y"}]},
        }
        s = fleet_affect_summary(states)
        assert s["fleet_health"] == "critical"
        assert len(s["safety_alerts"]) == 1


class TestEmotionCoordinates:
    def test_all_have_vad(self):
        for name, coords in EMOTION_COORDINATES.items():
            assert "v" in coords, f"{name} missing v"
            assert "a" in coords, f"{name} missing a"
            assert "d" in coords, f"{name} missing d"

    def test_all_have_cluster(self):
        for name, coords in EMOTION_COORDINATES.items():
            assert "cluster" in coords, f"{name} missing cluster"

    def test_plutchik_primaries(self):
        primaries = [n for n, c in EMOTION_COORDINATES.items() if c.get("plutchik")]
        assert len(primaries) == 8

    def test_opposites_far_apart_in_vad(self):
        """Opposites should be far in full VAD space, not just valence (fear/anger differ on dominance)."""
        for name, coords in EMOTION_COORDINATES.items():
            if "opposite" in coords:
                opp = coords["opposite"]
                opp_coords = EMOTION_COORDINATES[opp]
                # Full VAD distance
                d = (abs(coords["v"] - opp_coords["v"]) +
                     abs(coords["a"] - opp_coords["a"]) +
                     abs(coords["d"] - opp_coords["d"]))
                assert d > 0.5, f"{name} and {opp} not far enough apart in VAD space (d={d})"
