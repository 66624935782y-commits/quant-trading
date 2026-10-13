from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from quant_trading.market_data import CandleRequest, build_batch, make_demo_batch
from quant_trading import storage
from quant_trading.storage import StorageError, list_saved_batches, load_batch, save_batch


NOW = datetime(2026, 10, 13, 12, 30, tzinfo=timezone.utc)


def example(market="spot", symbol="BTC/USDT", timeframe="1h", limit=5):
    return make_demo_batch(CandleRequest(market, symbol, timeframe, limit), now=NOW)


def manifest(saved):
    return json.loads((saved.directory / "manifest.json").read_text(encoding="utf-8"))


def write_manifest(saved, metadata):
    (saved.directory / "manifest.json").write_text(json.dumps(metadata), encoding="utf-8")


@pytest.mark.parametrize("market", ["spot", "usdt_futures"])
@pytest.mark.parametrize("source", ["binance_public", "synthetic_demo"])
def test_roundtrip_preserves_typed_candles_and_metadata(tmp_path, market, source):
    original = example(market)
    batch = build_batch(original.request, original.raw_rows, source=source, fetched_at=NOW)
    saved = save_batch(batch, tmp_path)
    loaded = load_batch(saved.directory)
    assert saved.directory.parent == tmp_path / "raw" / market / "BTCUSDT" / "1h"
    assert loaded.request == batch.request
    assert loaded.raw_rows == batch.raw_rows
    assert loaded.fetched_at == NOW
    assert loaded.source == source
    assert loaded.warnings == batch.warnings
    pd.testing.assert_frame_equal(loaded.frame, batch.frame)
    assert str(loaded.frame["timestamp"].dtype) == "datetime64[ns, UTC]"
    assert all(str(loaded.frame[name].dtype) == "float64" for name in loaded.frame.columns[1:])
    metadata = manifest(saved)
    assert metadata == saved.metadata
    assert metadata["schema_version"] == 1
    assert metadata["raw_count"] == metadata["candle_count"] == 5
    assert metadata["fetched_at"] == NOW.isoformat()
    for filename, key in (("raw.json", "raw_sha256"), ("candles.csv", "csv_sha256")):
        assert hashlib.sha256((saved.directory / filename).read_bytes()).hexdigest() == metadata[key]


def test_raw_archive_keeps_unsorted_duplicates_extra_fields_and_open_candle(tmp_path):
    initial = example()
    raw = deepcopy(list(reversed(initial.raw_rows)))
    raw.append(deepcopy(raw[0]))
    open_candle = deepcopy(raw[0])
    open_candle[0] += 3_600_000
    open_candle.append("original vendor field")
    raw.append(open_candle)
    batch = build_batch(initial.request, raw, source="binance_public", fetched_at=NOW)
    saved = save_batch(batch, tmp_path)
    assert json.loads((saved.directory / "raw.json").read_text()) == raw
    loaded = load_batch(saved.directory)
    assert loaded.raw_rows == raw
    assert len(loaded.raw_rows) == 7
    assert len(loaded.frame) == 5
    assert loaded.warnings == batch.warnings
    assert manifest(saved)["raw_count"] == 7
    # Loading uses the original collection time, so the once-open candle stays excluded.
    assert loaded.fetched_at == NOW
    pd.testing.assert_frame_equal(loaded.frame, batch.frame)


def test_repeat_saves_are_append_only_and_market_source_are_distinguishable(tmp_path):
    spot_demo = example()
    spot_public = build_batch(spot_demo.request, spot_demo.raw_rows, fetched_at=NOW)
    futures_demo = example("usdt_futures", "ETH/USDT", "5m")
    saved = [save_batch(batch, tmp_path) for batch in
             (spot_demo, spot_demo, spot_public, futures_demo)]
    assert len({item.directory for item in saved}) == 4
    assert all(item.directory.is_dir() for item in saved)
    listed = list_saved_batches(tmp_path)
    assert {item.directory for item in listed} == {item.directory for item in saved}
    assert {(item.metadata["market"], item.metadata["source"]) for item in listed} == {
        ("spot", "synthetic_demo"), ("spot", "binance_public"),
        ("usdt_futures", "synthetic_demo"),
    }
    assert saved[-1].directory.parent == tmp_path / "raw" / "usdt_futures" / "ETHUSDT" / "5m"


def test_missing_storage_returns_empty_list_without_creating_folders(tmp_path):
    root = tmp_path / "not-created"
    assert list_saved_batches(root) == []
    assert not root.exists()


@pytest.mark.parametrize("filename", ["raw.json", "candles.csv"])
def test_modified_artifact_rejected_by_checksum(tmp_path, filename):
    saved = save_batch(example(), tmp_path)
    with (saved.directory / filename).open("ab") as handle:
        handle.write(b"altered")
    with pytest.raises(StorageError, match="校验失败"):
        load_batch(saved.directory)
    with pytest.raises(StorageError, match="校验失败"):
        list_saved_batches(tmp_path)


def test_rewritten_csv_checksum_does_not_allow_candles_to_disagree_with_raw(tmp_path):
    saved = save_batch(example(), tmp_path)
    csv_file = saved.directory / "candles.csv"
    content = csv_file.read_bytes().replace(b"50000", b"40000")
    assert content != csv_file.read_bytes()
    csv_file.write_bytes(content)
    metadata = manifest(saved)
    metadata["csv_sha256"] = hashlib.sha256(content).hexdigest()
    write_manifest(saved, metadata)
    with pytest.raises(StorageError, match="不一致"):
        load_batch(saved.directory)


def test_rewritten_raw_checksum_still_checks_derived_candles(tmp_path):
    saved = save_batch(example(), tmp_path)
    raw_file = saved.directory / "raw.json"
    rows = json.loads(raw_file.read_text())
    rows[0][5] = "99"
    content = json.dumps(rows).encode()
    raw_file.write_bytes(content)
    metadata = manifest(saved)
    metadata["raw_sha256"] = hashlib.sha256(content).hexdigest()
    write_manifest(saved, metadata)
    with pytest.raises(StorageError, match="不一致"):
        load_batch(saved.directory)


@pytest.mark.parametrize(("key", "value"), [
    ("schema_version", 2), ("schema_version", True),
    ("source", "live_account"), ("market", "../../other"),
    ("symbol", "../../other"), ("timeframe", "../../other"),
    ("limit", True), ("limit", 0), ("fetched_at", "2026-10-13T12:30:00"),
    ("fetched_at", "2026-10-13T13:30:00+01:00"),
    ("raw_sha256", "bad"), ("raw_count", True), ("candle_count", -1),
    ("warnings", "not a list"), ("warnings", ["wrong warning"]),
])
def test_invalid_or_inconsistent_manifest_rejected(tmp_path, key, value):
    saved = save_batch(example(), tmp_path)
    metadata = manifest(saved)
    metadata[key] = value
    write_manifest(saved, metadata)
    with pytest.raises(StorageError):
        load_batch(saved.directory)


@pytest.mark.parametrize("filename", ["raw.json", "candles.csv", "manifest.json"])
def test_missing_artifact_is_explicit_corruption(tmp_path, filename):
    saved = save_batch(example(), tmp_path)
    (saved.directory / filename).unlink()
    with pytest.raises(StorageError, match="不完整"):
        load_batch(saved.directory)
    with pytest.raises(StorageError, match="不完整"):
        list_saved_batches(tmp_path)


def test_manifest_cannot_add_a_path_to_redirect_reads(tmp_path):
    saved = save_batch(example(), tmp_path)
    metadata = manifest(saved)
    metadata["raw_path"] = "../../outside.json"
    write_manifest(saved, metadata)
    with pytest.raises(StorageError, match="字段"):
        load_batch(saved.directory)


def test_duplicate_manifest_json_fields_rejected(tmp_path):
    saved = save_batch(example(), tmp_path)
    path = saved.directory / "manifest.json"
    content = path.read_text()
    path.write_text(content.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1'))
    with pytest.raises(StorageError, match="重复字段"):
        load_batch(saved.directory)


def test_save_rejects_edited_dataframe_without_partial_archive(tmp_path):
    batch = example()
    batch.frame.loc[0, "close"] = 1
    with pytest.raises(StorageError, match="不一致"):
        save_batch(batch, tmp_path)
    assert list_saved_batches(tmp_path) == []


@pytest.mark.parametrize("failure_at", [1, 2, 3])
def test_write_failure_leaves_no_completed_or_pending_run(tmp_path, monkeypatch, failure_at):
    original_write = storage._write_bytes
    writes = 0

    def fail_write(path, content):
        nonlocal writes
        writes += 1
        if writes == failure_at:
            raise OSError("simulated disk failure")
        original_write(path, content)

    monkeypatch.setattr(storage, "_write_bytes", fail_write)
    with pytest.raises(StorageError, match="保存行情失败"):
        save_batch(example(), tmp_path)
    assert list_saved_batches(tmp_path) == []
    assert list(tmp_path.rglob("manifest.json")) == []
    assert list(tmp_path.rglob(".pending-*")) == []


def test_failed_atomic_rename_keeps_previous_archive_unchanged(tmp_path, monkeypatch):
    saved = save_batch(example(), tmp_path)
    previous = {path.name: path.read_bytes() for path in saved.directory.iterdir()}

    def fail_rename(_source, _destination):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(storage.os, "rename", fail_rename)
    with pytest.raises(StorageError, match="保存行情失败"):
        save_batch(example(), tmp_path)
    assert {path.name: path.read_bytes() for path in saved.directory.iterdir()} == previous
    assert len(list_saved_batches(tmp_path)) == 1
    assert list(tmp_path.rglob(".pending-*")) == []


def test_catalog_ignores_only_hidden_pending_runs(tmp_path):
    saved = save_batch(example(), tmp_path)
    pending = saved.directory.parent / ".pending-interrupted"
    pending.mkdir()
    (pending / "raw.json").write_text("unfinished")
    assert [item.directory for item in list_saved_batches(tmp_path)] == [saved.directory]
    incomplete = saved.directory.parent / "unexpected-run"
    incomplete.mkdir()
    with pytest.raises(StorageError, match="完整"):
        list_saved_batches(tmp_path)


def create_symlink(link: Path, target: Path, *, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip("This platform does not permit local symlink creation")


def test_save_and_catalog_reject_symlink_escape(tmp_path):
    root = tmp_path / "data"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    create_symlink(root / "raw", outside, directory=True)
    with pytest.raises(StorageError, match="符号链接"):
        save_batch(example(), root)
    with pytest.raises(StorageError, match="符号链接"):
        list_saved_batches(root)
    assert list(outside.iterdir()) == []


def test_load_rejects_artifact_symlink_even_with_matching_bytes(tmp_path):
    saved = save_batch(example(), tmp_path / "data")
    raw = saved.directory / "raw.json"
    outside = tmp_path / "outside.json"
    outside.write_bytes(raw.read_bytes())
    raw.unlink()
    create_symlink(raw, outside)
    with pytest.raises(StorageError, match="符号链接"):
        load_batch(saved.directory)
