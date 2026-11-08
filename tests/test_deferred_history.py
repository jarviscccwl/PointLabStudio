import unittest
from core.commands.history import History


class DeferredHistoryTests(unittest.TestCase):
    def test_failed_undo_preserves_entry_for_retry(self):
        history = History()
        outcome = [False]
        history.push("restore", lambda: outcome[0], lambda: None)
        self.assertIsNone(history.undo())
        self.assertTrue(history.can_undo)
        self.assertFalse(history.can_redo)
        outcome[0] = True
        self.assertEqual(history.undo(), "restore")
        self.assertFalse(history.can_undo)
        self.assertTrue(history.can_redo)

    def test_failed_redo_preserves_entry_for_retry(self):
        history = History()
        outcome = [False]
        history.push("trash", lambda: None, lambda: outcome[0])
        history.undo()
        self.assertIsNone(history.redo())
        self.assertTrue(history.can_redo)
        self.assertFalse(history.can_undo)
        outcome[0] = True
        self.assertEqual(history.redo(), "trash")
