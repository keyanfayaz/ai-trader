from __future__ import annotations

import base64
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.repositories.markdown_research_store import MarkdownResearchStore, ResearchRecord


class MarkdownResearchStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.base_dir = Path(temp_dir.name) / "research"
        self.store = MarkdownResearchStore(base_dir=str(self.base_dir), retention_days=14)

    def test_append_and_list_recent_records(self) -> None:
        created_at = datetime.now(UTC).isoformat()
        saved = self.store.append_record(
            ResearchRecord(
                created_at=created_at,
                source="test",
                symbols=["AAPL"],
                ticker="AAPL",
                timeframe="1Day",
                risk_profile="balanced",
                strategy_fingerprint="fp-1",
                signal_context=[{"symbol": "AAPL", "signal": "BUY"}],
                strategy={"picks": [{"symbol": "AAPL"}]},
                evaluation={"confidence": 0.8},
            )
        )
        records = self.store.list_recent_records(symbols=["AAPL"], timeframe="1Day")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["strategy_fingerprint"], "fp-1")
        self.assertEqual(records[0]["file_path"], saved["file_path"])

    def test_prune_old_entries_removes_expired_date_dirs(self) -> None:
        stale_date = (datetime.now(UTC) - timedelta(days=20)).date().isoformat()
        stale_dir = self.base_dir / stale_date
        stale_dir.mkdir(parents=True, exist_ok=True)
        (stale_dir / "old.md").write_text("old", encoding="utf-8")
        self.store.prune_old_entries(now=datetime.now(UTC))
        self.assertFalse(stale_dir.exists())

    def test_list_tree_and_update_record(self) -> None:
        created_at = datetime.now(UTC).isoformat()
        saved = self.store.append_record(
            ResearchRecord(
                created_at=created_at,
                source="test",
                symbols=["AAPL"],
                ticker="AAPL",
                timeframe="1Day",
                risk_profile="balanced",
                strategy_fingerprint="fp-2",
                signal_context=[{"symbol": "AAPL", "signal": "BUY"}],
                strategy={"picks": [{"symbol": "AAPL"}]},
                evaluation={"confidence": 0.7},
            )
        )
        relative_path = str(Path(saved["file_path"]).relative_to(self.base_dir))
        record_id = base64.urlsafe_b64encode(relative_path.encode("utf-8")).decode("ascii").rstrip("=")
        tree = self.store.list_record_tree()
        self.assertEqual(len(tree), 1)
        self.assertEqual(len(tree[0]["records"]), 1)
        detail = self.store.get_record(record_id=record_id)
        self.assertIn("# Market Research Snapshot", detail["markdown"])
        old_version = detail["version"]
        updated = self.store.update_record(
            record_id=record_id,
            markdown="## Edited\n\nNew notes.",
            expected_version=old_version,
        )
        self.assertIn("## Edited", updated["markdown"])
        self.assertNotEqual(old_version, updated["version"])
        self.assertTrue(updated["markdown"].startswith("<!-- RESEARCH_META "))

    def test_invalid_record_id_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.get_record(record_id="Li4vLi4vZXRjL3Bhc3N3ZA")

    def test_delete_record_removes_file(self) -> None:
        created_at = datetime.now(UTC).isoformat()
        saved = self.store.append_record(
            ResearchRecord(
                created_at=created_at,
                source="test",
                symbols=["AAPL"],
                ticker="AAPL",
                timeframe="1Day",
                risk_profile="balanced",
                strategy_fingerprint="fp-delete",
                signal_context=[{"symbol": "AAPL", "signal": "BUY"}],
                strategy={"picks": [{"symbol": "AAPL"}]},
                evaluation={"confidence": 0.7},
            )
        )
        relative_path = str(Path(saved["file_path"]).relative_to(self.base_dir))
        record_id = base64.urlsafe_b64encode(relative_path.encode("utf-8")).decode("ascii").rstrip("=")
        deleted = self.store.delete_record(record_id=record_id)
        self.assertTrue(deleted["deleted"])
        with self.assertRaises(FileNotFoundError):
            self.store.get_record(record_id=record_id)

    def test_delete_records_and_folder(self) -> None:
        created_at = datetime.now(UTC).isoformat()
        saved_one = self.store.append_record(
            ResearchRecord(
                created_at=created_at,
                source="test",
                symbols=["AAPL"],
                ticker="AAPL",
                timeframe="1Day",
                risk_profile="balanced",
                strategy_fingerprint="fp-bulk-1",
                signal_context=[{"symbol": "AAPL", "signal": "BUY"}],
                strategy={"picks": [{"symbol": "AAPL"}]},
                evaluation={"confidence": 0.6},
            )
        )
        saved_two = self.store.append_record(
            ResearchRecord(
                created_at=created_at,
                source="test",
                symbols=["MSFT"],
                ticker="MSFT",
                timeframe="1Day",
                risk_profile="balanced",
                strategy_fingerprint="fp-bulk-2",
                signal_context=[{"symbol": "MSFT", "signal": "BUY"}],
                strategy={"picks": [{"symbol": "MSFT"}]},
                evaluation={"confidence": 0.6},
            )
        )
        record_ids = []
        for saved in (saved_one, saved_two):
            relative_path = str(Path(saved["file_path"]).relative_to(self.base_dir))
            record_ids.append(base64.urlsafe_b64encode(relative_path.encode("utf-8")).decode("ascii").rstrip("="))
        bulk = self.store.delete_records(record_ids=[record_ids[0], "missing"])
        self.assertEqual(bulk["deleted_count"], 1)
        self.assertEqual(len(bulk["missing_ids"]), 1)

        # Recreate one file then delete by folder.
        self.store.append_record(
            ResearchRecord(
                created_at=created_at,
                source="test",
                symbols=["NVDA"],
                ticker="NVDA",
                timeframe="1Day",
                risk_profile="balanced",
                strategy_fingerprint="fp-bulk-3",
                signal_context=[{"symbol": "NVDA", "signal": "BUY"}],
                strategy={"picks": [{"symbol": "NVDA"}]},
                evaluation={"confidence": 0.6},
            )
        )
        folder = datetime.now(UTC).date().isoformat()
        deleted_folder = self.store.delete_folder(folder=folder)
        self.assertGreaterEqual(deleted_folder["deleted_count"], 1)


if __name__ == "__main__":
    unittest.main()
