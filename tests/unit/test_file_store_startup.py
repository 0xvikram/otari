"""Whether a files backend that cannot be built stops the boot depends on ``files_enabled``.

An operator who turns files off and removes the bucket the settings point at
should still have a gateway. One who leaves files on should hear about the
broken backend before the first upload fails.
"""

import logging
from pathlib import Path

import pytest
from fastapi import FastAPI

from gateway.adapters.file_storage_adapter import LocalDirFileStore
from gateway.container import build_container
from gateway.core.config import GatewayConfig
from gateway.main import _create_lifespan, _resolve_file_store


def test_files_off_starts_without_a_store_it_cannot_build(caplog: pytest.LogCaptureFixture) -> None:
    config = GatewayConfig(files_enabled=False, files_backend="s3", files_s3_bucket=None)
    # The gateway logger does not propagate once logging is configured, so
    # listen on it directly rather than on the root logger.
    gateway_logger = logging.getLogger("gateway")
    gateway_logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.WARNING, logger="gateway"):
            store = _resolve_file_store(config, build_container(config=config))
    finally:
        gateway_logger.removeHandler(caplog.handler)

    assert store is None
    assert any("files backend cannot be built" in record.getMessage() for record in caplog.records)


def test_files_on_still_refuses_a_store_it_cannot_build() -> None:
    config = GatewayConfig(files_enabled=True, files_backend="s3", files_s3_bucket=None)

    with pytest.raises(ValueError, match="files_s3_bucket"):
        _resolve_file_store(config, build_container(config=config))


def test_files_off_keeps_a_store_it_can_build(tmp_path: Path) -> None:
    """A stored ``file_id`` still resolves with the upload routes off, as before."""
    config = GatewayConfig(files_enabled=False, files_backend="local", files_local_dir=str(tmp_path))

    store = _resolve_file_store(config, build_container(config=config))

    assert isinstance(store, LocalDirFileStore)


@pytest.mark.asyncio
async def test_lifespan_starts_with_files_off_and_no_bucket(tmp_path: Path) -> None:
    """End to end: the opt-out an operator does on Railway, files off and the bucket gone."""
    config = GatewayConfig(
        database_url=f"sqlite:///{tmp_path / 'files-off.db'}",
        master_key="sk-test-master",
        files_enabled=False,
        files_backend="s3",
        files_s3_bucket=None,
    )
    app = FastAPI()
    app.state.config = config
    app.state.enabled_features = ()
    app.state.container = build_container(config=config)

    async with _create_lifespan()(app):
        assert app.state.file_store is None


def _single_node_levels(config: GatewayConfig, caplog: pytest.LogCaptureFixture, advisory_level: int) -> list[int]:
    """The levels the single-node note was logged at while ``config``'s store was built."""
    gateway_logger = logging.getLogger("gateway")
    gateway_logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.DEBUG, logger="gateway"):
            _resolve_file_store(config, build_container(config=config), advisory_level)
    finally:
        gateway_logger.removeHandler(caplog.handler)
    return [record.levelno for record in caplog.records if "single node" in record.getMessage()]


def test_files_on_a_local_directory_warns_that_it_serves_a_single_node(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Each replica holds its own directory, so a second one 404s a file the first stored."""
    config = GatewayConfig(files_enabled=True, files_backend="local", files_local_dir=str(tmp_path))

    assert _single_node_levels(config, caplog, logging.WARNING) == [logging.WARNING]


def test_a_first_start_holds_the_single_node_warning_back(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A zero-config first start stays quiet, as it does for the other setup advisories."""
    config = GatewayConfig(files_enabled=True, files_backend="local", files_local_dir=str(tmp_path))

    assert _single_node_levels(config, caplog, logging.DEBUG) == [logging.DEBUG]


def test_files_off_on_a_local_directory_says_nothing_of_a_single_node(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = GatewayConfig(files_enabled=False, files_backend="local", files_local_dir=str(tmp_path))

    assert _single_node_levels(config, caplog, logging.WARNING) == []
