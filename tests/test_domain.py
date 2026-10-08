"""Behavioural checks for the independent Xiuxian rule package."""
from __future__ import annotations
from dataclasses import replace
from types import SimpleNamespace
import unittest

from plux_plugins.xiuxian.domain.commands import parse_command
from plux_plugins.xiuxian.domain.config import DEFAULT_GAME_CONFIG, RealmDropWeights
from plux_plugins.xiuxian.domain.artifact_effects import breakthrough_costs


class DomainTests(unittest.TestCase):
    def message(self, text: str, *, mention_state: str = "none",
                mentioned_ids: tuple[str, ...] = ()):
        return SimpleNamespace(message_kind="text", content=text,
                               conversation_id="group@chatroom",
                               mention_state=mention_state,
                               mentioned_ids=mentioned_ids)

    def test_named_and_targeted_commands_require_native_mention_evidence(self):
        self.assertEqual(("profile", "青玄"), (
            parse_command(self.message("#修仙 青玄")).kind,
            parse_command(self.message("#修仙 青玄")).argument))
        text = "#决斗 @道友\u2005 10"
        self.assertEqual("usage", parse_command(self.message(text)).kind)
        command = parse_command(self.message(text, mention_state="explicit_other",
                                             mentioned_ids=("player2",)))
        self.assertEqual(("pvp_duel", "player2", 10),
                         (command.kind, command.target_id, command.amount))

    def test_default_values_and_rule_validation_are_preserved(self):
        self.assertEqual(100, DEFAULT_GAME_CONFIG.initial_spirit_stones)
        self.assertEqual((100, 250, 500), DEFAULT_GAME_CONFIG.breakthrough_cultivation)
        self.assertEqual((50, 30, 15, 5), (
            DEFAULT_GAME_CONFIG.drop_weights[0].artifact,
            DEFAULT_GAME_CONFIG.drop_weights[0].spirit,
            DEFAULT_GAME_CONFIG.drop_weights[0].ancient,
            DEFAULT_GAME_CONFIG.drop_weights[0].treasure))
        with self.assertRaises(ValueError):
            replace(DEFAULT_GAME_CONFIG, support_minimum=101,
                    support_maximum=100)
        with self.assertRaises(ValueError):
            RealmDropWeights(50, 30, 15, 6)

    def test_artifact_bonus_does_not_change_base_cost_without_item(self):
        self.assertEqual((100, 100), breakthrough_costs(100, 100, ()))


if __name__ == "__main__":
    unittest.main()

