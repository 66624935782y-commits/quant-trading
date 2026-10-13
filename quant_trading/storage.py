"""Append-only local market archives; saved data never supplies executable paths."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from uuid import uuid4

from quant_trading.market_data import CandleRequest, MarketBatch, build_batch


class StorageError(ValueError):
    """Invalid/corrupt market archive, including a wrapped local I/O failure."""


@dataclass(frozen=True)
class SavedBatch:
    directory: Path
    metadata: dict


_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]
_FIELDS = {
    "schema_version", "market", "symbol", "timeframe", "limit", "source",
    "fetched_at", "warnings", "raw_count", "candle_count", "raw_sha256",
    "csv_sha256",
}
_RUN_ID = re.compile(r"\d{8}T\d{12}Z_[0-9a-f]{32}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_SOURCES = {"binance_public", "synthetic_demo"}


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(Path(path)))


def _check_path(path: Path) -> None:
    # Check every existing ancestor before any read, mkdir or directory scan.
    # A copied/downloaded archive cannot redirect the app through a symlink.
    for component in reversed((path, *path.parents)):
        if component.is_symlink():
            raise StorageError("行情存储路径包含符号链接，请使用普通本地文件夹。")


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
            + "\n").encode("utf-8")


def _csv_bytes(batch: MarketBatch) -> bytes:
    frame = batch.frame.copy()
    if list(frame.columns) != _COLUMNS:
        raise StorageError("行情表格字段不完整。")
    frame["timestamp"] = frame["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return frame.to_csv(index=False, lineterminator="\n", float_format="%.17g").encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write_bytes(path: Path, content: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _symbol_directory(symbol: str) -> str:
    return symbol.replace("/", "")


def save_batch(batch: MarketBatch, root: Path = Path("data")) -> SavedBatch:
    """Save raw response, derived candles and checksums in a new atomic archive.

    An interrupted/failed write leaves no visible completed run. Existing runs
    are never edited; hidden pending directories are excluded from the catalog.
    """
    pending: Path | None = None
    try:
        # Revalidate data rather than trusting a mutable DataFrame in the batch.
        validated = build_batch(batch.request, batch.raw_rows, source=batch.source,
                                fetched_at=batch.fetched_at)
        csv_content = _csv_bytes(validated)
        if csv_content != _csv_bytes(batch) or tuple(validated.warnings) != tuple(batch.warnings):
            raise StorageError("行情批次与原始响应不一致，未保存。")
        raw_content = _json_bytes(batch.raw_rows)
        request = validated.request
        metadata = {
            "schema_version": 1,
            "market": request.market,
            "symbol": request.symbol,
            "timeframe": request.timeframe,
            "limit": request.limit,
            "source": validated.source,
            "fetched_at": validated.fetched_at.astimezone(timezone.utc).isoformat(),
            "warnings": list(validated.warnings),
            "raw_count": len(validated.raw_rows),
            "candle_count": len(validated.frame),
            "raw_sha256": _sha256(raw_content),
            "csv_sha256": _sha256(csv_content),
        }
        root = _absolute(root)
        parent = root / "raw" / request.market / _symbol_directory(request.symbol) / request.timeframe
        _check_path(parent)
        parent.mkdir(parents=True, exist_ok=True)
        _check_path(parent)
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid4().hex
        destination = parent / run_id
        if destination.exists():
            raise StorageError("新的存储编号发生冲突，请重新保存。")
        pending = Path(tempfile.mkdtemp(prefix=".pending-", dir=parent))
        _write_bytes(pending / "raw.json", raw_content)
        _write_bytes(pending / "candles.csv", csv_content)
        _write_bytes(pending / "manifest.json", _json_bytes(metadata))
        _check_path(parent)
        if destination.exists():
            raise StorageError("存储编号发生冲突，已有数据未覆盖。")
        os.rename(pending, destination)
        pending = None
        return SavedBatch(destination, metadata)
    except StorageError:
        raise
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as error:
        raise StorageError("保存行情失败；请检查数据格式、文件夹权限和磁盘空间。") from error
    finally:
        if pending is not None:
            shutil.rmtree(pending, ignore_errors=True)


def _read_bytes(path: Path, maximum: int) -> bytes:
    _check_path(path)
    if not path.is_file():
        raise StorageError(f"行情存档不完整：缺少 {path.name}。")
    with path.open("rb") as handle:
        content = handle.read(maximum + 1)
    if len(content) > maximum:
        raise StorageError(f"行情存档异常：{path.name} 超出支持大小。")
    return content


def _unique_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise StorageError("行情存档 JSON 包含重复字段。")
        result[key] = value
    return result


def _parse_json(content: bytes) -> object:
    def invalid_constant(_: str) -> None:
        raise StorageError("行情存档 JSON 包含非有限数字。")

    return json.loads(content.decode("utf-8"), object_pairs_hook=_unique_keys,
                      parse_constant=invalid_constant)


def _metadata_request(metadata: object) -> tuple[CandleRequest, datetime]:
    if not isinstance(metadata, dict) or set(metadata) != _FIELDS:
        raise StorageError("行情存档描述文件字段缺失或不受支持。")
    if type(metadata["schema_version"]) is not int or metadata["schema_version"] != 1:
        raise StorageError("不支持此行情存档版本。")
    if not isinstance(metadata["source"], str) or metadata["source"] not in _SOURCES:
        raise StorageError("行情存档的数据来源不受支持。")
    if (not isinstance(metadata["warnings"], list)
            or any(not isinstance(warning, str) for warning in metadata["warnings"])):
        raise StorageError("行情存档警告信息格式损坏。")
    for key in ("raw_count", "candle_count"):
        if type(metadata[key]) is not int or metadata[key] < 0:
            raise StorageError("行情存档数量信息损坏。")
    for key in ("raw_sha256", "csv_sha256"):
        if not isinstance(metadata[key], str) or not _HASH.fullmatch(metadata[key]):
            raise StorageError("行情存档校验信息损坏。")
    request = CandleRequest(metadata["market"], metadata["symbol"],
                            metadata["timeframe"], metadata["limit"])
    if not isinstance(metadata["fetched_at"], str):
        raise StorageError("行情存档时间格式损坏。")
    fetched_at = datetime.fromisoformat(metadata["fetched_at"])
    if fetched_at.tzinfo is None or fetched_at.utcoffset() != timedelta(0):
        raise StorageError("行情存档获取时间必须为 UTC 时间。")
    return request, fetched_at


def _load_verified(directory: Path) -> tuple[MarketBatch, dict]:
    _check_path(directory)
    if not directory.is_dir() or not _RUN_ID.fullmatch(directory.name):
        raise StorageError("请选择完整的行情批次文件夹。")
    metadata = _parse_json(_read_bytes(directory / "manifest.json", 64 * 1024))
    request, fetched_at = _metadata_request(metadata)
    expected_parents = (request.timeframe, _symbol_directory(request.symbol), request.market, "raw")
    if tuple(parent.name for parent in list(directory.parents)[:4]) != expected_parents:
        raise StorageError("行情存档路径与描述信息不一致。")
    raw_content = _read_bytes(directory / "raw.json", 32 * 1024 * 1024)
    csv_content = _read_bytes(directory / "candles.csv", 4 * 1024 * 1024)
    if (_sha256(raw_content) != metadata["raw_sha256"]
            or _sha256(csv_content) != metadata["csv_sha256"]):
        raise StorageError("行情存档校验失败，文件已损坏或被修改。")
    raw_rows = _parse_json(raw_content)
    if not isinstance(raw_rows, list) or len(raw_rows) != metadata["raw_count"]:
        raise StorageError("原始行情数量与存档描述不一致。")
    batch = build_batch(request, raw_rows, source=metadata["source"], fetched_at=fetched_at)
    if (len(batch.frame) != metadata["candle_count"]
            or list(batch.warnings) != metadata["warnings"]
            or _csv_bytes(batch) != csv_content):
        raise StorageError("行情存档的原始响应、K 线或描述信息不一致。")
    return batch, metadata


def load_batch(directory: Path) -> MarketBatch:
    """Verify checksums and reconstruct candles from the archived raw response."""
    try:
        return _load_verified(_absolute(directory))[0]
    except StorageError:
        raise
    except (OSError, ValueError, TypeError, AttributeError, KeyError, IndexError) as error:
        raise StorageError("读取行情失败；存档格式损坏或本地文件无法访问。") from error


def _directories(parent: Path) -> list[Path]:
    _check_path(parent)
    directories = []
    for child in parent.iterdir():
        _check_path(child)
        if child.name.startswith(".pending-"):
            continue
        if not child.is_dir():
            raise StorageError("行情存储目录结构损坏，存在异常文件。")
        directories.append(child)
    return sorted(directories)


def list_saved_batches(root: Path = Path("data")) -> list[SavedBatch]:
    """List verified completed batches; corrupt archives produce a clear error."""
    try:
        raw_root = _absolute(root) / "raw"
        _check_path(raw_root)
        if not raw_root.exists():
            return []
        if not raw_root.is_dir():
            raise StorageError("行情存储的 raw 路径必须是文件夹。")
        saved = []
        for market in _directories(raw_root):
            for symbol in _directories(market):
                for timeframe in _directories(symbol):
                    for directory in _directories(timeframe):
                        _, metadata = _load_verified(directory)
                        saved.append(SavedBatch(directory, metadata))
        return sorted(saved, key=lambda batch: (batch.metadata["fetched_at"], str(batch.directory)),
                      reverse=True)
    except StorageError:
        raise
    except (OSError, ValueError, TypeError, AttributeError, KeyError, IndexError) as error:
        raise StorageError("读取行情目录失败；请检查存档格式和文件夹权限。") from error
