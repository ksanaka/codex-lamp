import asyncio
from contextlib import suppress
import fcntl
import os

from bleak.backends.device import BLEDevice
import pytest

from codex_lamp.config import load_settings
from codex_lamp.daemon import BleakLampTransport, _daemon_logger, apply_state, main, run_daemon
from codex_lamp.protocol import NUS_TX_UUID
from codex_lamp.state import StateStore


class RecordingTransport:
    def __init__(self):
        self.commands: list[str] = []

    async def send(self, command: str) -> None:
        self.commands.append(command)


class DaemonTransport(RecordingTransport):
    def __init__(self):
        super().__init__()
        self.connected = False
        self.connect_count = 0
        self.discover_count = 0
        self.close_count = 0
        self.on_send = None

    async def discover(self, settings):
        self.discover_count += 1
        return "lamp"

    async def connect(self, device):
        self.connect_count += 1
        self.connected = True

    async def send(self, command):
        await super().send(command)
        if self.on_send is not None:
            self.on_send(command)

    @property
    def is_connected(self):
        return self.connected

    async def close(self):
        self.close_count += 1
        self.connected = False


def test_apply_state_sends_commands_in_device_order(tmp_path):
    # Break caught: reordering or omitting commands while applying one desired state.
    transport = RecordingTransport()

    asyncio.run(apply_state(transport, "input", load_settings(tmp_path)))

    assert transport.commands == ["LEDON", "BRIGH120", "COLOR200000255"]


def test_discovery_prefers_the_configured_uuid_over_a_name_match(tmp_path, monkeypatch):
    # Break caught: connecting to the first similarly named lamp despite an explicit paired-device UUID.
    name_match = BLEDevice("other-uuid", "MOONSIDE-Halo-A", {})
    uuid_match = BLEDevice("chosen-uuid", "Desk Lamp", {})

    async def discover_devices():
        return [name_match, uuid_match]

    monkeypatch.setattr("codex_lamp.daemon.BleakScanner.discover", discover_devices)
    settings = load_settings(tmp_path).with_updates(device_uuid="CHOSEN-UUID")

    selected = asyncio.run(BleakLampTransport().discover(settings))

    assert selected is uuid_match


def test_discovery_uses_the_name_prefix_without_a_configured_uuid(tmp_path, monkeypatch):
    # Break caught: selecting an unrelated advertisement when automatic name matching is configured.
    unrelated = BLEDevice("other-uuid", "Desk Lamp", {})
    name_match = BLEDevice("lamp-uuid", "MOONSIDE-Halo", {})

    async def discover_devices():
        return [unrelated, name_match]

    monkeypatch.setattr("codex_lamp.daemon.BleakScanner.discover", discover_devices)

    selected = asyncio.run(BleakLampTransport().discover(load_settings(tmp_path)))

    assert selected is name_match


def test_bleak_transport_writes_utf8_to_the_nus_characteristic(tmp_path, monkeypatch):
    # Break caught: using a fire-and-forget write, wrong characteristic, or non-byte payload.
    device = BLEDevice("lamp-uuid", "MOONSIDE-Halo", {})
    writes: list[tuple[str, bytes, bool]] = []

    class FakeBleakClient:
        def __init__(self, selected_device):
            self.selected_device = selected_device
            self.is_connected = False

        async def connect(self):
            self.is_connected = True

        async def write_gatt_char(self, characteristic, data, *, response):
            writes.append((characteristic, data, response))

        async def disconnect(self):
            self.is_connected = False

    monkeypatch.setattr("codex_lamp.daemon.BleakClient", FakeBleakClient)
    transport = BleakLampTransport()

    async def exercise_transport():
        await transport.connect(device)
        await transport.send("LEDON")
        assert transport.is_connected
        await transport.close()

    asyncio.run(exercise_transport())

    assert writes == [(NUS_TX_UUID, b"LEDON", True)]
    assert not transport.is_connected


def test_daemon_suppresses_an_unchanged_effective_state_and_turns_off_on_shutdown(tmp_path):
    # Break caught: resending the same state every 200 ms or leaving the lamp on at graceful shutdown.
    settings = load_settings(tmp_path).with_updates(poll_interval=0.001)
    StateStore(settings).update("session-a", "input", 1)
    transport = DaemonTransport()
    stop_event = asyncio.Event()

    async def exercise_daemon():
        task = asyncio.create_task(run_daemon(settings, transport, stop_event=stop_event))
        while transport.commands.count("COLOR200000255") < 1:
            await asyncio.sleep(0)
        await asyncio.sleep(0.01)
        stop_event.set()
        assert await task is True

    asyncio.run(exercise_daemon())

    assert transport.commands == ["LEDON", "BRIGH120", "COLOR200000255", "LEDOFF"]


def test_daemon_reconnects_and_replays_the_current_state_after_disconnect(tmp_path):
    # Break caught: retaining the duplicate marker across a lost connection, leaving a reconnected lamp stale.
    settings = load_settings(tmp_path).with_updates(poll_interval=0.001)
    StateStore(settings).update("session-a", "input", 1)
    transport = DaemonTransport()
    stop_event = asyncio.Event()

    def disconnect_then_stop(command):
        if command != "COLOR200000255":
            return
        if transport.commands.count(command) == 1:
            transport.connected = False
        else:
            stop_event.set()

    transport.on_send = disconnect_then_stop

    result = asyncio.run(run_daemon(settings, transport, stop_event=stop_event, backoff_delays=(0,)))

    assert result is True
    assert transport.connect_count == 2
    assert transport.commands == [
        "LEDON",
        "BRIGH120",
        "COLOR200000255",
        "LEDON",
        "BRIGH120",
        "COLOR200000255",
        "LEDOFF",
    ]


def test_second_daemon_exits_while_the_owner_retains_its_pid(tmp_path):
    # Break caught: blocking on the process lock or letting a rejected daemon remove the owner's PID file.
    settings = load_settings(tmp_path).with_updates(poll_interval=0.001)
    StateStore(settings).update("session-a", "idle", 1)
    owner_transport = DaemonTransport()
    rejected_transport = DaemonTransport()
    owner_stop = asyncio.Event()

    async def exercise_lock():
        owner_task = asyncio.create_task(run_daemon(settings, owner_transport, stop_event=owner_stop))
        while not owner_transport.commands:
            await asyncio.sleep(0)

        assert (tmp_path / "daemon.pid").read_text(encoding="ascii") == f"{os.getpid()}\n"
        assert await run_daemon(settings, rejected_transport, stop_event=asyncio.Event()) is False
        assert (tmp_path / "daemon.pid").read_text(encoding="ascii") == f"{os.getpid()}\n"

        owner_stop.set()
        assert await owner_task is True

    asyncio.run(exercise_lock())

    assert rejected_transport.discover_count == 0
    assert not (tmp_path / "daemon.pid").exists()


def test_daemon_uses_bounded_backoff_then_rescans(tmp_path, monkeypatch):
    # Break caught: retrying a stale device forever or using an unbounded/non-exponential retry schedule.
    settings = load_settings(tmp_path)
    stop_event = asyncio.Event()
    observed_delays: list[float] = []

    class FailingTransport(DaemonTransport):
        async def discover(self, settings):
            self.discover_count += 1
            if self.discover_count == 2:
                stop_event.set()
            return f"lamp-{self.discover_count}"

        async def connect(self, device):
            self.connect_count += 1
            raise ConnectionError(device)

    async def record_wait(event, delay):
        observed_delays.append(delay)
        return False

    monkeypatch.setattr("codex_lamp.daemon._wait_for_stop", record_wait)
    transport = FailingTransport()

    assert asyncio.run(run_daemon(settings, transport, stop_event=stop_event)) is True

    assert observed_delays == [1.0, 2.0, 4.0, 8.0, 16.0]
    assert transport.connect_count == 5
    assert transport.discover_count == 2


def test_daemon_watches_state_at_the_configured_interval(tmp_path, monkeypatch):
    # Break caught: busy-polling the state file or ignoring the configured 200 ms cadence.
    settings = load_settings(tmp_path)
    StateStore(settings).update("session-a", "idle", 1)
    transport = DaemonTransport()
    stop_event = asyncio.Event()
    observed_delays: list[float] = []

    async def stop_after_first_poll(event, delay):
        observed_delays.append(delay)
        event.set()
        return True

    monkeypatch.setattr("codex_lamp.daemon._wait_for_stop", stop_after_first_poll)

    assert asyncio.run(run_daemon(settings, transport, stop_event=stop_event)) is True

    assert observed_delays == [0.2]


def test_main_exits_cleanly_and_logs_when_the_lock_is_held(tmp_path, monkeypatch):
    # Break caught: a detached second process hanging or reporting failure instead of yielding to the owner.
    monkeypatch.setenv("CODEX_LAMP_HOME", str(tmp_path))
    lock_path = tmp_path / "daemon.lock"
    tmp_path.mkdir(exist_ok=True)

    with lock_path.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert main() == 0

    assert "daemon already running" in (tmp_path / "logs" / "daemon.log").read_text(
        encoding="utf-8"
    )
    assert not (tmp_path / "daemon.pid").exists()


def test_daemon_log_rotates_instead_of_growing_without_bound(tmp_path):
    # Break caught: using an unbounded file handler for a persistent process's repeated diagnostics.
    load_settings(tmp_path)
    logger = _daemon_logger(tmp_path)

    logger.info("%s", "x" * 600_000)
    logger.info("%s", "y" * 600_000)
    for handler in logger.handlers:
        handler.flush()

    assert (tmp_path / "logs" / "daemon.log.1").exists()


def test_stop_interrupts_hung_discovery_and_cleans_the_pid(tmp_path):
    # Break caught: SIGTERM only setting a flag while an unbounded discovery keeps the daemon and PID alive.
    settings = load_settings(tmp_path)
    stop_event = asyncio.Event()

    class HungDiscoveryTransport(DaemonTransport):
        def __init__(self):
            super().__init__()
            self.discovery_started = asyncio.Event()

        async def discover(self, settings):
            self.discovery_started.set()
            await asyncio.Event().wait()

    transport = HungDiscoveryTransport()

    async def exercise_shutdown():
        task = asyncio.create_task(run_daemon(settings, transport, stop_event=stop_event))
        await transport.discovery_started.wait()
        assert (tmp_path / "daemon.pid").exists()
        stop_event.set()
        done, _ = await asyncio.wait({task}, timeout=0.1)
        try:
            assert task in done
            assert await task is True
            assert not (tmp_path / "daemon.pid").exists()
        finally:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                (tmp_path / "daemon.pid").unlink(missing_ok=True)

    asyncio.run(exercise_shutdown())


def test_hung_write_times_out_then_reconnects_and_replays(tmp_path, monkeypatch):
    # Break caught: one stalled acknowledged GATT write permanently preventing reconnect and state replay.
    monkeypatch.setattr("codex_lamp.daemon.BLE_OPERATION_TIMEOUT_SECONDS", 0.01, raising=False)
    settings = load_settings(tmp_path)
    StateStore(settings).update("session-a", "input", 1)
    stop_event = asyncio.Event()

    class HungFirstWriteTransport(DaemonTransport):
        def __init__(self):
            super().__init__()
            self.first_write_started = asyncio.Event()

        async def send(self, command):
            if self.connect_count == 1 and command == "LEDON":
                self.first_write_started.set()
                await asyncio.Event().wait()
            await super().send(command)
            if command == "COLOR200000255":
                stop_event.set()

    transport = HungFirstWriteTransport()

    async def exercise_recovery():
        task = asyncio.create_task(
            run_daemon(settings, transport, stop_event=stop_event, backoff_delays=(0,))
        )
        await transport.first_write_started.wait()
        done, _ = await asyncio.wait({task}, timeout=0.2)
        try:
            assert task in done
            assert await task is True
        finally:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                (tmp_path / "daemon.pid").unlink(missing_ok=True)

    asyncio.run(exercise_recovery())

    assert transport.connect_count == 2
    assert transport.commands == ["LEDON", "BRIGH120", "COLOR200000255", "LEDOFF"]


def test_hung_shutdown_operations_are_bounded_and_pid_is_cleaned(tmp_path, monkeypatch):
    # Break caught: a hung LEDOFF or disconnect await preventing the owner from releasing its PID and lock.
    monkeypatch.setattr("codex_lamp.daemon.BLE_CLEANUP_TIMEOUT_SECONDS", 0.01, raising=False)
    settings = load_settings(tmp_path)
    StateStore(settings).update("session-a", "idle", 1)
    stop_event = asyncio.Event()

    class HungCleanupTransport(DaemonTransport):
        def __init__(self):
            super().__init__()
            self.off_attempted = asyncio.Event()
            self.close_attempted = asyncio.Event()

        async def send(self, command):
            if command == "LEDOFF":
                self.off_attempted.set()
                await asyncio.Event().wait()
            await super().send(command)
            if command == "COLOR255180050":
                stop_event.set()

        async def close(self):
            self.close_attempted.set()
            await asyncio.Event().wait()

    transport = HungCleanupTransport()

    async def exercise_shutdown():
        task = asyncio.create_task(run_daemon(settings, transport, stop_event=stop_event))
        done, _ = await asyncio.wait({task}, timeout=0.2)
        try:
            assert task in done
            assert await task is True
            assert transport.off_attempted.is_set()
            assert transport.close_attempted.is_set()
            assert not (tmp_path / "daemon.pid").exists()
        finally:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                (tmp_path / "daemon.pid").unlink(missing_ok=True)

    asyncio.run(exercise_shutdown())


def test_connect_failure_after_link_establishment_remains_closable(monkeypatch):
    # Break caught: publishing client ownership only after service discovery succeeds, leaking a partial link.
    device = BLEDevice("lamp-uuid", "MOONSIDE-Halo", {})
    disconnect_count = 0

    class PartialConnectClient:
        def __init__(self, selected_device):
            self.is_connected = False

        async def connect(self):
            self.is_connected = True
            raise ConnectionError("service discovery failed")

        async def disconnect(self):
            nonlocal disconnect_count
            disconnect_count += 1
            self.is_connected = False

    monkeypatch.setattr("codex_lamp.daemon.BleakClient", PartialConnectClient)
    transport = BleakLampTransport()

    async def exercise_partial_connection():
        with pytest.raises(ConnectionError, match="service discovery"):
            await transport.connect(device)
        assert transport.is_connected
        await transport.close()

    asyncio.run(exercise_partial_connection())

    assert disconnect_count == 1
    assert not transport.is_connected


def test_disconnect_failure_retains_client_for_cleanup_retry(monkeypatch):
    # Break caught: clearing client ownership before disconnect succeeds, making a transient failure unrecoverable.
    device = BLEDevice("lamp-uuid", "MOONSIDE-Halo", {})
    disconnect_count = 0

    class RetryableDisconnectClient:
        def __init__(self, selected_device):
            self.is_connected = False

        async def connect(self):
            self.is_connected = True

        async def disconnect(self):
            nonlocal disconnect_count
            disconnect_count += 1
            if disconnect_count == 1:
                raise ConnectionError("temporary disconnect failure")
            self.is_connected = False

    monkeypatch.setattr("codex_lamp.daemon.BleakClient", RetryableDisconnectClient)
    transport = BleakLampTransport()

    async def exercise_cleanup_retry():
        await transport.connect(device)
        with pytest.raises(ConnectionError, match="temporary disconnect"):
            await transport.close()
        assert transport.is_connected
        await transport.close()

    asyncio.run(exercise_cleanup_retry())

    assert disconnect_count == 2
    assert not transport.is_connected


def test_daemon_owns_cancellation_resistant_discovery_until_it_settles(tmp_path):
    # Break caught: abandoning a canceled BLE task that later blocks asyncio.run() loop shutdown as an orphan.
    settings = load_settings(tmp_path)
    stop_event = asyncio.Event()

    class SettlingDiscoveryTransport(DaemonTransport):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.cancel_seen = asyncio.Event()
            self.allow_settle = asyncio.Event()
            self.settled = False

        async def discover(self, settings):
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancel_seen.set()
                await self.allow_settle.wait()
                self.settled = True
                raise

    transport = SettlingDiscoveryTransport()

    async def exercise_owned_cancellation():
        task = asyncio.create_task(run_daemon(settings, transport, stop_event=stop_event))
        await transport.started.wait()
        stop_event.set()
        await transport.cancel_seen.wait()
        done, _ = await asyncio.wait({task}, timeout=0.02)
        try:
            assert task not in done
        finally:
            transport.allow_settle.set()
        assert await task is True
        assert transport.settled
        assert not (tmp_path / "daemon.pid").exists()

    asyncio.run(exercise_owned_cancellation())


def test_cancellation_during_final_cleanup_still_closes_and_removes_pid(tmp_path, monkeypatch):
    # Break caught: CancelledError escaping LEDOFF cleanup before close, PID removal, and lock release execute.
    monkeypatch.setattr("codex_lamp.daemon.BLE_CLEANUP_TIMEOUT_SECONDS", 0.01)
    settings = load_settings(tmp_path)
    StateStore(settings).update("session-a", "idle", 1)
    stop_event = asyncio.Event()

    class CanceledCleanupTransport(DaemonTransport):
        def __init__(self):
            super().__init__()
            self.off_started = asyncio.Event()
            self.close_attempted = False

        async def send(self, command):
            if command == "LEDOFF":
                self.off_started.set()
                await asyncio.Event().wait()
            await super().send(command)
            if command == "COLOR255180050":
                stop_event.set()

        async def close(self):
            self.close_attempted = True
            self.connected = False

    transport = CanceledCleanupTransport()

    async def exercise_cleanup_cancellation():
        task = asyncio.create_task(run_daemon(settings, transport, stop_event=stop_event))
        await transport.off_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert transport.close_attempted
        assert not (tmp_path / "daemon.pid").exists()

    asyncio.run(exercise_cleanup_cancellation())


def test_timed_out_connect_settles_then_disconnects_a_late_link(monkeypatch):
    # Break caught: clearing a not-yet-connected client while its canceled connect later establishes a live link.
    monkeypatch.setattr("codex_lamp.daemon.BLE_OPERATION_TIMEOUT_SECONDS", 0.01)
    device = BLEDevice("lamp-uuid", "MOONSIDE-Halo", {})
    clients = []

    class LateConnectClient:
        def __init__(self, selected_device):
            self.is_connected = False
            self.disconnect_count = 0
            clients.append(self)

        async def connect(self):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(0.02)
                self.is_connected = True

        async def disconnect(self):
            self.disconnect_count += 1
            self.is_connected = False

    monkeypatch.setattr("codex_lamp.daemon.BleakClient", LateConnectClient)
    transport = BleakLampTransport()

    async def exercise_late_connection():
        with pytest.raises(TimeoutError):
            await transport.connect(device)
        await transport.close()
        await asyncio.sleep(0.03)

    asyncio.run(exercise_late_connection())

    assert clients[0].disconnect_count == 1
    assert not clients[0].is_connected
    assert not transport.is_connected


def test_same_turn_owner_cancel_and_child_settlement_propagates_cancellation(
    tmp_path, monkeypatch
):
    # Break caught: replacing owner cancellation with a normal timeout when the child settles in the same turn.
    monkeypatch.setattr("codex_lamp.daemon.BLE_OPERATION_TIMEOUT_SECONDS", 0.01)
    settings = load_settings(tmp_path)
    stop_event = asyncio.Event()
    owner: dict[str, asyncio.Task] = {}

    class SameTurnCancelTransport(DaemonTransport):
        def __init__(self):
            super().__init__()
            self.close_attempted = False

        async def discover(self, settings):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                stop_event.set()
                owner["task"].cancel()
                raise

        async def close(self):
            self.close_attempted = True
            self.connected = False

    transport = SameTurnCancelTransport()

    async def exercise_same_turn_race():
        daemon_task = asyncio.create_task(
            run_daemon(settings, transport, stop_event=stop_event, backoff_delays=(0,))
        )
        owner["task"] = daemon_task
        with pytest.raises(asyncio.CancelledError):
            await daemon_task

    asyncio.run(exercise_same_turn_race())

    assert transport.close_attempted
    assert not (tmp_path / "daemon.pid").exists()
    with (tmp_path / "daemon.lock").open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
