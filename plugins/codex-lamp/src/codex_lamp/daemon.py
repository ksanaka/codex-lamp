"""Persistent Bluetooth transport and lifecycle for Codex Lamp."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
import fcntl
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
from typing import Any, Awaitable, Protocol, TypeVar

from bleak import BleakClient, BleakScanner

from .config import Settings, ensure_layout, load_settings, resolve_home
from .protocol import NUS_TX_UUID, commands_for_state
from .state import StateStore


BACKOFF_DELAYS = (1.0, 2.0, 4.0, 8.0, 16.0)
# Bleak's default scan is five seconds; ten seconds permits that scan plus a
# local connection/write handshake while still guaranteeing recovery.
BLE_OPERATION_TIMEOUT_SECONDS = 10.0
# Shutdown gets a short best-effort window for LEDOFF and disconnect before
# process-lock and PID cleanup must continue.
BLE_CLEANUP_TIMEOUT_SECONDS = 2.0

_Result = TypeVar("_Result")
_operation_is_bounded: ContextVar[bool] = ContextVar(
    "codex_lamp_operation_is_bounded", default=False
)


class _StopRequested(Exception):
    """Internal control flow raised when shutdown wins an operation race."""


class LampTransport(Protocol):
    """Async boundary used by the daemon to drive one lamp."""

    async def discover(self, settings: Settings) -> Any | None: ...

    async def connect(self, device: Any) -> None: ...

    async def send(self, command: str) -> None: ...

    @property
    def is_connected(self) -> bool: ...

    async def close(self) -> None: ...


class BleakLampTransport:
    """Bleak-backed Moonside Nordic UART transport."""

    def __init__(self) -> None:
        self._client: BleakClient | None = None

    async def discover(self, settings: Settings) -> Any | None:
        devices = await _bounded_operation(
            BleakScanner.discover(), timeout=BLE_OPERATION_TIMEOUT_SECONDS
        )
        if settings.device_uuid is not None:
            expected_uuid = settings.device_uuid.casefold()
            return next(
                (device for device in devices if device.address.casefold() == expected_uuid),
                None,
            )
        return next(
            (
                device
                for device in devices
                if device.name is not None and device.name.startswith(settings.device_name_prefix)
            ),
            None,
        )

    async def connect(self, device: Any) -> None:
        await self.close()
        client = BleakClient(device)
        self._client = client
        await _bounded_operation(client.connect(), timeout=BLE_OPERATION_TIMEOUT_SECONDS)

    async def send(self, command: str) -> None:
        if self._client is None or not self._client.is_connected:
            raise ConnectionError("lamp is not connected")
        await _bounded_operation(
            self._client.write_gatt_char(
                NUS_TX_UUID, command.encode("utf-8"), response=True
            ),
            timeout=BLE_OPERATION_TIMEOUT_SECONDS,
        )

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    async def close(self) -> None:
        client = self._client
        if client is None:
            return
        if not client.is_connected:
            self._client = None
            return
        try:
            await _bounded_operation(
                client.disconnect(), timeout=BLE_CLEANUP_TIMEOUT_SECONDS
            )
        except BaseException:
            if not client.is_connected and self._client is client:
                self._client = None
            raise
        if client.is_connected:
            raise ConnectionError("lamp remained connected after disconnect")
        if self._client is client:
            self._client = None


async def apply_state(transport: LampTransport, state: str, settings: Settings) -> None:
    """Send every command required to apply one desired lamp state."""
    for command in commands_for_state(state, settings):
        await _bounded_operation(
            transport.send(command), timeout=BLE_OPERATION_TIMEOUT_SECONDS
        )


async def run_daemon(
    settings: Settings | None = None,
    transport: LampTransport | None = None,
    *,
    stop_event: asyncio.Event | None = None,
    backoff_delays: tuple[float, ...] = BACKOFF_DELAYS,
) -> bool:
    """Own the process lock and maintain the lamp until graceful shutdown.

    Return ``False`` when another daemon already owns the data directory.
    Duplicate suppression belongs here rather than in :func:`apply_state`: a
    freshly connected lamp must always receive the complete current state.
    """
    active_settings = settings or load_settings()
    active_transport = transport or BleakLampTransport()
    active_stop_event = stop_event or asyncio.Event()
    home = ensure_layout(active_settings.home)
    logger = _daemon_logger(home)
    lock_path = home / "daemon.lock"
    pid_path = home / "daemon.pid"

    with lock_path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.info("daemon already running")
            return False

        pid_path.write_text(f"{os.getpid()}\n", encoding="ascii")
        logger.info("daemon started pid=%s", os.getpid())
        try:
            await _maintain_lamp(
                active_settings,
                active_transport,
                active_stop_event,
                backoff_delays,
                logger,
            )
            return True
        finally:
            cleanup_task = asyncio.create_task(
                _shutdown_transport(active_transport, logger)
            )
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                while not cleanup_task.done():
                    try:
                        await asyncio.shield(cleanup_task)
                    except asyncio.CancelledError:
                        continue
                raise
            finally:
                pid_path.unlink(missing_ok=True)
                logger.info("daemon stopped")
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


async def _shutdown_transport(
    transport: LampTransport, logger: logging.Logger
) -> None:
    if transport.is_connected:
        try:
            await _bounded_operation(
                transport.send("LEDOFF"), timeout=BLE_CLEANUP_TIMEOUT_SECONDS
            )
        except BaseException as error:
            logger.warning("failed to turn lamp off: %s", error)
    try:
        await _bounded_operation(
            transport.close(), timeout=BLE_CLEANUP_TIMEOUT_SECONDS
        )
    except BaseException as error:
        logger.warning("failed to close lamp transport: %s", error)


async def _maintain_lamp(
    settings: Settings,
    transport: LampTransport,
    stop_event: asyncio.Event,
    backoff_delays: tuple[float, ...],
    logger: logging.Logger,
) -> None:
    if not backoff_delays:
        raise ValueError("backoff_delays must not be empty")

    store = StateStore(settings)
    discovery_failures = 0
    while not stop_event.is_set():
        try:
            device = await _bounded_operation(
                transport.discover(settings),
                timeout=BLE_OPERATION_TIMEOUT_SECONDS,
                stop_event=stop_event,
            )
        except _StopRequested:
            return
        except Exception as error:
            device = None
            logger.warning("lamp discovery failed: %s", error)

        if device is None:
            delay = backoff_delays[min(discovery_failures, len(backoff_delays) - 1)]
            discovery_failures = min(discovery_failures + 1, len(backoff_delays) - 1)
            if await _wait_for_stop(stop_event, delay):
                return
            continue

        discovery_failures = 0
        for delay in backoff_delays:
            if stop_event.is_set():
                return
            try:
                await _bounded_operation(
                    transport.connect(device),
                    timeout=BLE_OPERATION_TIMEOUT_SECONDS,
                    stop_event=stop_event,
                )
                logger.info("lamp connected")
                if await _watch_state(settings, store, transport, stop_event):
                    return
                raise ConnectionError("lamp disconnected")
            except _StopRequested:
                return
            except Exception as error:
                logger.warning("lamp connection lost: %s", error)
            finally:
                if not stop_event.is_set():
                    try:
                        await _bounded_operation(
                            transport.close(), timeout=BLE_CLEANUP_TIMEOUT_SECONDS
                        )
                    except Exception as error:
                        logger.warning("failed to reset lamp transport: %s", error)

            if await _wait_for_stop(stop_event, delay):
                return


async def _watch_state(
    settings: Settings,
    store: StateStore,
    transport: LampTransport,
    stop_event: asyncio.Event,
) -> bool:
    last_applied_state: str | None = None
    while not stop_event.is_set():
        if not transport.is_connected:
            return False
        desired_state = store.effective()
        if desired_state != last_applied_state:
            await _bounded_operation(
                apply_state(transport, desired_state, settings),
                timeout=BLE_OPERATION_TIMEOUT_SECONDS,
                stop_event=stop_event,
            )
            last_applied_state = desired_state
        if await _wait_for_stop(stop_event, settings.poll_interval):
            return True
    return True


async def _wait_for_stop(stop_event: asyncio.Event, delay: float) -> bool:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay)
    except TimeoutError:
        return False
    return True


async def _bounded_operation(
    operation: Awaitable[_Result],
    *,
    timeout: float,
    stop_event: asyncio.Event | None = None,
) -> _Result:
    """Own one operation through completion or fully settled cancellation."""
    if _operation_is_bounded.get():
        return await operation

    context_token = _operation_is_bounded.set(True)
    operation_task = asyncio.ensure_future(operation)
    stop_task = (
        asyncio.create_task(stop_event.wait()) if stop_event is not None else None
    )
    waiters = {operation_task}
    if stop_task is not None:
        waiters.add(stop_task)
    try:
        done, _ = await asyncio.wait(
            waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        if operation_task in done:
            return operation_task.result()
        if stop_task is not None and stop_task in done:
            await _cancel_and_drain(operation_task)
            raise _StopRequested
        await _cancel_and_drain(operation_task)
        raise TimeoutError(f"BLE operation exceeded {timeout:g} seconds")
    except asyncio.CancelledError:
        await _cancel_and_drain(operation_task)
        raise
    finally:
        if stop_task is not None:
            if not stop_task.done():
                stop_task.cancel()
            try:
                await stop_task
            except BaseException:
                pass
        _operation_is_bounded.reset(context_token)


async def _cancel_and_drain(task: asyncio.Future[Any]) -> None:
    """Cancel an owned task and do not return while it can outlive the loop."""
    if not task.done():
        task.cancel()
    interrupted: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.wait({task})
        except asyncio.CancelledError as error:
            interrupted = error
            task.cancel()
    try:
        task.result()
    except BaseException:
        pass
    if interrupted is not None:
        raise interrupted


def _daemon_logger(home: Path) -> logging.Logger:
    logger = logging.getLogger(f"codex_lamp.daemon.{home}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = RotatingFileHandler(
            home / "logs" / "daemon.log",
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger


async def _run_with_signals(settings: Settings) -> bool:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed_signals: list[signal.Signals] = []
    for requested_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(requested_signal, stop_event.set)
        except (NotImplementedError, RuntimeError):
            continue
        installed_signals.append(requested_signal)
    try:
        return await run_daemon(settings, stop_event=stop_event)
    finally:
        for installed_signal in installed_signals:
            loop.remove_signal_handler(installed_signal)


def main() -> int:
    """Run the daemon module, yielding cleanly when an owner already exists."""
    try:
        asyncio.run(_run_with_signals(load_settings()))
    except KeyboardInterrupt:
        return 0
    except Exception:
        home = ensure_layout(resolve_home())
        _daemon_logger(home).exception("daemon stopped unexpectedly")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
