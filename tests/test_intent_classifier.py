"""Tests for grimoire.core.intent_classifier — keyword-based intent routing."""

from __future__ import annotations

import unittest

from grimoire.core.intent_classifier import IntentClassifier, IntentMatch


class TestIntentMatch(unittest.TestCase):
    def test_to_dict(self) -> None:
        m = IntentMatch(
            intent="dev",
            confidence=0.85,
            keywords_matched=("implement", "code"),
            fallbacks=(("architect", 0.45),),
        )
        d = m.to_dict()
        self.assertEqual(d["intent"], "dev")
        self.assertAlmostEqual(d["confidence"], 0.85)
        self.assertIn("implement", d["keywords_matched"])
        self.assertEqual(len(d["fallbacks"]), 1)

    def test_frozen(self) -> None:
        m = IntentMatch(intent="dev", confidence=0.5, keywords_matched=(), fallbacks=())
        with self.assertRaises(AttributeError):
            m.intent = "qa"  # type: ignore[misc]


class TestIntentClassifierDev(unittest.TestCase):
    def setUp(self) -> None:
        self.clf = IntentClassifier()

    def test_implement_feature(self) -> None:
        result = self.clf.classify("implement the login function")
        self.assertEqual(result.intent, "dev")
        self.assertGreater(result.confidence, 0.3)
        self.assertIn("implement", result.keywords_matched)

    def test_fix_bug(self) -> None:
        result = self.clf.classify("fix bug in the authentication module")
        self.assertEqual(result.intent, "dev")

    def test_refactor(self) -> None:
        result = self.clf.classify("refactor the database layer")
        self.assertEqual(result.intent, "dev")
        self.assertIn("refactor", result.keywords_matched)

    def test_tdd(self) -> None:
        result = self.clf.classify("use tdd to implement sorting")
        self.assertEqual(result.intent, "dev")

    def test_french_dev(self) -> None:
        result = self.clf.classify("implémenter la fonctionnalité de connexion")
        self.assertEqual(result.intent, "dev")


class TestIntentClassifierArchitect(unittest.TestCase):
    def setUp(self) -> None:
        self.clf = IntentClassifier()

    def test_architecture(self) -> None:
        result = self.clf.classify("design the system architecture for microservices")
        self.assertEqual(result.intent, "architect")

    def test_adr(self) -> None:
        result = self.clf.classify("write an adr for the database choice")
        self.assertEqual(result.intent, "architect")

    def test_tech_debt(self) -> None:
        result = self.clf.classify("analyze tech debt in the coupling between modules")
        self.assertEqual(result.intent, "architect")


class TestIntentClassifierPM(unittest.TestCase):
    def setUp(self) -> None:
        self.clf = IntentClassifier()

    def test_prd(self) -> None:
        result = self.clf.classify("create the prd for the new product")
        self.assertEqual(result.intent, "pm")

    def test_roadmap(self) -> None:
        result = self.clf.classify("build the roadmap with priorities")
        self.assertEqual(result.intent, "pm")


class TestIntentClassifierQA(unittest.TestCase):
    def setUp(self) -> None:
        self.clf = IntentClassifier()

    def test_test_plan(self) -> None:
        result = self.clf.classify("create a test plan for the api")
        self.assertEqual(result.intent, "qa")

    def test_regression(self) -> None:
        result = self.clf.classify("check regression on the payment module")
        self.assertEqual(result.intent, "qa")


class TestIntentClassifierSM(unittest.TestCase):
    def setUp(self) -> None:
        self.clf = IntentClassifier()

    def test_sprint(self) -> None:
        result = self.clf.classify("plan the next sprint with velocity tracking")
        self.assertEqual(result.intent, "sm")


class TestIntentClassifierFallbacks(unittest.TestCase):
    def setUp(self) -> None:
        self.clf = IntentClassifier()

    def test_ambiguous_has_fallbacks(self) -> None:
        # "implement" hits dev, "test plan" hits qa, "sprint" hits sm
        result = self.clf.classify("implement the test plan for the next sprint")
        self.assertTrue(len(result.fallbacks) > 0 or result.confidence < 1.0)

    def test_no_match_defaults_dev(self) -> None:
        result = self.clf.classify("do something completely unrelated to anything")
        self.assertEqual(result.intent, "dev")
        self.assertEqual(result.confidence, 0.0)


class TestIntentClassifierMulti(unittest.TestCase):
    def setUp(self) -> None:
        self.clf = IntentClassifier()

    def test_classify_multi(self) -> None:
        results = self.clf.classify_multi(
            "implement the architecture with a test plan and sprint planning",
            top_k=4,
        )
        self.assertGreater(len(results), 1)
        intents = {r.intent for r in results}
        self.assertTrue(intents)

    def test_top_k_limits(self) -> None:
        results = self.clf.classify_multi("implement everything", top_k=2)
        self.assertLessEqual(len(results), 2)


class TestIntentClassifierCustomKeywords(unittest.TestCase):
    def test_custom_extends(self) -> None:
        clf = IntentClassifier(
            custom_keywords={
                "devops": (
                    ("pipeline", 0.9),
                    ("deploy", 0.9),
                    ("ci/cd", 0.9),
                ),
            },
        )
        result = clf.classify("set up the deploy pipeline")
        self.assertEqual(result.intent, "devops")

    def test_known_intents_includes_custom(self) -> None:
        clf = IntentClassifier(
            custom_keywords={"devops": (("pipeline", 0.9),)},
        )
        self.assertIn("devops", clf.known_intents)


class TestIntentClassifierMeta(unittest.TestCase):
    def test_known_intents(self) -> None:
        clf = IntentClassifier()
        intents = clf.known_intents
        self.assertIn("dev", intents)
        self.assertIn("architect", intents)
        self.assertIn("pm", intents)
        self.assertIn("qa", intents)
        self.assertIn("sm", intents)


if __name__ == "__main__":
    unittest.main()
