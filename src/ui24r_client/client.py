from __future__ import annotations

import asyncio
import base64
from collections import defaultdict
from contextlib import aclosing, asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import (
    AsyncContextManager,
    AsyncGenerator,
    Generator,
    Generic,
    NewType,
    TypeVar,
)

import aiohttp
from aiohttp import ClientWebSocketResponse
from anyio import (
    WouldBlock,
    create_memory_object_stream,
    create_task_group,
    move_on_after,
)
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

from .settings import BoolSetting, FaderSetting, SettingType, StrSetting

__all__ = ["Ui24RClient", "Input", "Aux"]

SessionId = NewType("SessionId", int)


@dataclass
class _SettingsSubscriber:
    session_id: SessionId
    queue: MemoryObjectSendStream[str]
    last_value: str | None = None

    def send_nowait(self, value: str) -> None:
        if value != self.last_value:
            self.queue.send_nowait(value)
            self.last_value = value


@dataclass
class _AllSettingsSubscriber:
    session_id: SessionId
    queue: MemoryObjectSendStream[tuple[str, str]]


_T = TypeVar("_T")


@dataclass
class ValueStream(Generic[_T]):
    value: _T | None = None
    _events: list[tuple[asyncio.Event, SessionId | None]] = field(default_factory=list)

    def publish(self, value: _T, session_id: SessionId | None) -> None:
        """
        Publish new VU meter values.
        (to be called when processing the web socket output.)
        """
        self.value = value
        for event, sid in self._events:
            # Unblock events for sessions different from the one where it was
            # published.
            if sid is None or sid != session_id:
                event.set()

    def get_nowait(self) -> _T:
        if self.value is None:
            raise UnknownSettingValueError

        return self.value

    async def get(self) -> _T:
        if self.value is not None:
            return self.value

        event = asyncio.Event()
        self._events.append((event, None))

        try:
            await event.wait()
            assert self.value is not None
            return self.value
        finally:
            self._events.remove((event, None))

    @asynccontextmanager
    async def subscribe(
        self, session_id: SessionId | None
    ) -> AsyncGenerator[AsyncGenerator[_T, None], None]:
        """
        Subscribe to the VU meter output.

        Usage::

            async with stream.subscribe() as iterator:
                async for item in iterator:
                    print(item)
        """
        event = asyncio.Event()
        self._events.append((event, session_id))

        if self.value is not None:
            event.set()

        try:

            async def generator() -> AsyncGenerator[_T, None]:
                last_value: _T | None = None
                while True:
                    await event.wait()
                    if self.value is not None and self.value != last_value:
                        yield self.value
                        last_value = self.value
                    event.clear()

            async with aclosing(generator()) as g:
                yield g
        finally:
            self._events.remove((event, session_id))


_default_session_id = SessionId(-1)


@dataclass
class Ui24RClient:
    """
    Main ui24 client class.

    Example usage::

        async with Ui24RClient.create(ip_or_hostname) as client:
            async with client.session() as session:
                input2 = session.input(2)
                await input2.mute()
    """

    ip: str
    closed: bool = False

    _settings: dict[str, ValueStream[str]] = field(
        default_factory=lambda: defaultdict(ValueStream)
    )
    _output_queue: asyncio.Queue[tuple[str, asyncio.Future[None]]] = field(
        default_factory=asyncio.Queue
    )
    _all_settings_subscribers: list[_AllSettingsSubscriber] = field(
        default_factory=list
    )

    _vu_input_meters: dict[int, ValueStream[InputVUMeterValues]] = field(
        default_factory=lambda: defaultdict(ValueStream)
    )
    _vu_player_meters: dict[int, ValueStream[InputVUMeterValues]] = field(
        default_factory=lambda: defaultdict(ValueStream)
    )

    # Raw VU data. Useful to subscribe to this stream if we want to send the
    # raw VU data to the front-end of another web application.
    _vu2_value_stream: ValueStream[str] = field(default_factory=ValueStream)

    _next_session_id: SessionId = SessionId(0)

    @classmethod
    @asynccontextmanager
    async def create(cls, ip: str) -> AsyncGenerator[Ui24RClient, None]:
        async with create_task_group() as tg:
            instance = cls(ip=ip)
            tg.start_soon(instance._run)
            try:
                yield instance

                # Somehow when we terminate, a little delay is needed in order
                # to flush everything. (waiting until the queue is empty is not
                # sufficient.)
                async with move_on_after(2):
                    await asyncio.sleep(1)
            finally:
                # Mark as closed, stop accepting commands.
                instance.closed = True

                # Cancel everything.
                tg.cancel_scope.cancel()

    async def _run(self) -> None:
        url = f"http://{self.ip}/socket.io/1/websocket"

        while True:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(url) as websocket:
                    async with create_task_group() as tg:
                        tg.start_soon(lambda: self._receive_loop(websocket))
                        tg.start_soon(lambda: self._ping_loop(websocket))
                        tg.start_soon(lambda: self._send_loop(websocket))

    async def _ping_loop(self, websocket: ClientWebSocketResponse) -> None:
        while True:
            await websocket.send_str("3:::ALIVE")
            await asyncio.sleep(1)

    async def _receive_loop(self, websocket: ClientWebSocketResponse) -> None:
        async for message in websocket:
            if message.type == aiohttp.WSMsgType.TEXT:
                data = message.data

                if data.startswith("2::"):
                    # Ignore.   Echo???
                    await websocket.send_str(data)
                    continue

                # Remove '3:::' prefix
                data = data.removeprefix("3:::")

                for line in data.splitlines():
                    self._handle_message(line)

    async def _send_loop(self, websocket: ClientWebSocketResponse) -> None:
        while True:
            message, future = await self._output_queue.get()
            try:
                await websocket.send_str("3:::" + message)
                future.set_result(None)
            except BaseException as e:
                future.set_exception(e)

    def _handle_message(self, message: str) -> None:
        if message.startswith(("SETD^", "SETS^")):
            self._handle_setd_or_sets(message)
            return

        if message.startswith("VU2^"):
            self._handle_vu2(message)

        if message.startswith(("RTA^", "VUA^")):
            # Skip data for visualizing audio.
            return

    def _handle_setd_or_sets(self, value: str) -> None:
        """
        Handle messages like:

        SETD^var.mtk.currentTrackPos^0
        SETD^var.currentLength^-1
        SETD^m.eq.peak.l.9^0.5
        """
        setd_or_sets, key, value = value.split("^")
        self._settings[key].publish(value, None)

        for all_subscriber in self._all_settings_subscribers:
            try:
                all_subscriber.queue.send_nowait((key, value))
            except WouldBlock:
                pass

    async def set_key(
        self,
        key: str,
        value: str,
        command: str = "SETD",
        session_id: SessionId = _default_session_id,
    ) -> None:
        message = f"{command}^{key}^{value}"

        # Send key to other sessions.
        self._settings[key].publish(value, session_id)

        # Publish setting to subscribers.
        for all_subscriber in self._all_settings_subscribers:
            if all_subscriber.session_id != session_id:
                try:
                    all_subscriber.queue.send_nowait((key, value))
                except WouldBlock:
                    pass

        await self.send_message(message)

    def _handle_vu2(self, message: str) -> None:
        vu2, str_data = message.split("^")
        self._vu2_value_stream.publish(str_data, None)

        # Thanks: https://github.com/MatthewInch/UI24RBridge/blob/master/UI24RController/UI24RChannels/UI24RVUMessage.cs
        # (MIT licensed).
        data = memoryview(base64.b64decode(str_data))

        # Read header.
        ninputs = data[0]
        nmedia = data[1]
        nsubgroups = data[2]
        nfx = data[3]
        naux = data[4]
        nmasters = data[5]
        nlinein = data[6]
        zerovalue = data[7]

        # Skip over header now.
        data = data[7:]

        # 0-23: input channels
        for i in range(ninputs):
            meter = self._vu_input_meters[i]
            meter.publish(
                InputVUMeterValues(
                    pre=data[0],
                    post=data[1],
                    post_fader=data[2],
                    gate_in=data[3],
                    comp_out=data[4],
                    comp_meter=data[5],
                ),
                None,
            )
            data = data[6:]

        # 26-27: Player L/R
        for i in range(nmedia):
            meter = self._vu_player_meters[i]
            meter.publish(
                InputVUMeterValues(
                    pre=data[0],
                    post=data[1],
                    post_fader=data[2],
                    gate_in=data[3],
                    comp_out=data[4],
                    comp_meter=data[5],
                ),
                None,
            )
            data = data[6:]

        # 32-37: Subgroups
        # TODO...

    async def send_message(self, message: str) -> None:
        """
        Send message to ui24.

        The message will be put in a queue, but we'll wait until it has been
        transmitted, in order to have back pressure.
        """
        if self.closed:
            raise Exception("Ui24RClient instance was already closed.")

        future: asyncio.Future[None] = asyncio.Future()
        await self._output_queue.put((message, future))
        await future

    def get_setting_nowait(self, key: str) -> str:
        return self._settings[key].get_nowait()

    async def get_setting(self, key: str) -> str:
        return await self._settings[key].get()

    def subscribe(
        self,
        key: str,
        *,
        session_id: SessionId = _default_session_id,
    ) -> AsyncContextManager[AsyncGenerator[str, None]]:
        """
        Subscribe to one particular setting.
        """
        return self._settings[key].subscribe(session_id=session_id)

    @asynccontextmanager
    async def subscribe_all(
        self,
        *,
        session_id: SessionId = _default_session_id,
    ) -> AsyncGenerator[MemoryObjectReceiveStream[tuple[str, str]], None]:
        """
        Subscribe to any setting change.

        Usage::

            async for key, value in client.subscribe():
                print(key, value)
        """
        send_stream: MemoryObjectSendStream[tuple[str, str]]
        receive_stream: MemoryObjectReceiveStream[tuple[str, str]]

        # Take a decent buffer here, because when there are multiple
        # subscribers, they won't consume all at the same speed. We publish
        # values, using `send_nowait`, so we discard items when the buffer is
        # full.
        send_stream, receive_stream = create_memory_object_stream(
            50  # , item_type=tuple[str, str]
        )

        settings_subscriber = _AllSettingsSubscriber(
            queue=send_stream,
            session_id=session_id,
        )
        async with send_stream:
            self._all_settings_subscribers.append(settings_subscriber)

            try:
                async with receive_stream:
                    yield receive_stream
            finally:
                self._all_settings_subscribers.remove(settings_subscriber)

    def subscribe_raw_vu2_stream(
        self,
    ) -> AsyncContextManager[AsyncGenerator[str, None]]:
        """
        Subscribe to the raw VU data stream. Useful for sending the raw VU data
        to the front-end of another web application.
        """
        return self._vu2_value_stream.subscribe(None)

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        self._next_session_id = SessionId(self._next_session_id + 1)
        yield Session(client=self, session_id=self._next_session_id)

    def input(self, num: int) -> Input:
        return Session(client=self, session_id=_default_session_id).input(num)

    def aux(self, num: int) -> Aux:
        return Session(client=self, session_id=_default_session_id).aux(num)


@dataclass
class Session:
    client: Ui24RClient
    session_id: SessionId

    def input(self, num: int) -> Input:
        """Input, 0-base indexed on purpose, because
        `client.subscribe('i.0.mix')` is also 0-based, and there is not much we
        can do about that.."""
        assert 0 <= num <= 999  # XXX: What is the max value?
        return Input(self, num)

    def aux(self, num: int) -> Aux:
        assert 0 <= num <= 999  # XXX: What is the max value?
        return Aux(self, num)

    def get_setting_nowait(self, key: str) -> str:
        return self.client.get_setting_nowait(key)

    async def get_setting(self, key: str) -> str:
        return await self.client.get_setting(key)

    def subscribe(self, key: str) -> AsyncContextManager[AsyncGenerator[str, None]]:
        """
        Subscribe to one particular setting.

        Usage::

            async for value in client.subscribe(key):
                ...
        """
        return self.client.subscribe(key, session_id=self.session_id)

    def subscribe_all(
        self,
    ) -> AsyncContextManager[MemoryObjectReceiveStream[tuple[str, str]]]:
        """
        Subscribe to any setting change.

        Usage::

            async for key, value in client.subscribe():
                print(key, value)
        """
        return self.client.subscribe_all(session_id=self.session_id)

    def subscribe_raw_vu2_stream(
        self,
    ) -> AsyncContextManager[AsyncGenerator[str, None]]:
        return self.client.subscribe_raw_vu2_stream()

    async def set_key(self, key: str, value: str, command: str = "SETD") -> None:
        await self.client.set_key(key, value, command, session_id=self.session_id)


@dataclass
class Input:
    session: Session
    num: int

    @property
    def name(self) -> StrSetting:
        return StrSetting(
            self.session, key=f"i.{self.num}.name", setting_type=SettingType.SETS
        )

    @property
    def mute(self) -> BoolSetting:
        return BoolSetting(self.session, key=f"i.{self.num}.mute")

    @property
    def solo(self) -> BoolSetting:
        return BoolSetting(self.session, key=f"i.{self.num}.solo")

    @property
    def fader(self) -> FaderSetting:
        """
        The fader for this input. 0 means -inf, 1 means 10dB.
        Something like 0.7 means 0dB.
        """
        return FaderSetting(self.session, key=f"i.{self.num}.mix")

    @property
    def pan(self) -> FaderSetting:
        """
        The fader for this input. 0 means left, 1 means right.
        """
        return FaderSetting(self.session, key=f"i.{self.num}.pan")

    @property
    def gate(self) -> Gate:
        return Gate(self.session, self.num)

    @property
    def compressor(self) -> Compressor:
        return Compressor(self.session, self.num)

    @property
    def eq(self) -> Equalizer:
        """
        Equalizer for this input.
        """
        return Equalizer(self.session, self.num)

    def aux(self, number: int) -> FaderSetting:
        """
        Aux send level.
        """
        assert 0 <= number <= 9
        return FaderSetting(self.session, key=f"i.{self.num}.aux.{number}.value")

    @property
    def vu_meter(self) -> VUMeter[InputVUMeterValues]:
        return VUMeter(self.session.client._vu_input_meters[self.num])


@dataclass
class Aux:
    session: Session
    num: int

    @property
    def name(self) -> StrSetting:
        return StrSetting(
            self.session, key=f"a.{self.num}.name", setting_type=SettingType.SETS
        )

    @property
    def mute(self) -> BoolSetting:
        return BoolSetting(self.session, key=f"a.{self.num}.mute")

    @property
    def solo(self) -> BoolSetting:
        return BoolSetting(self.session, key=f"a.{self.num}.solo")

    @property
    def fader(self) -> FaderSetting:
        """
        The fader for this input. 0 means -inf, 1 means 10dB.
        Something like 0.7 means 0dB.
        """
        return FaderSetting(self.session, key=f"a.{self.num}.mix")


@dataclass
class Equalizer:
    session: Session
    num: int

    @property
    def hpf(self) -> FaderSetting:
        """
        High pass filter.
        0=20Hz, 0.5588=1kHz (that seems to be the max value).
        """
        return FaderSetting(self.session, key=f"i.{self.num}.eq.hpf.freq")

    @property
    def lpf(self) -> FaderSetting:
        """
        Low pass filter.
        0.5588=1kHz (that seems to be the min value), 1=22kHz.
        """
        return FaderSetting(self.session, key=f"i.{self.num}.eq.lpf.freq")

    @property
    def bypass(self) -> BoolSetting:
        """
        EQ bypass
        """
        return BoolSetting(self.session, key=f"i.{self.num}.eq.bypass")

    @property
    def invert(self) -> BoolSetting:
        """
        EQ invert.
        """
        return BoolSetting(self.session, key=f"i.{self.num}.eq.invert")


@dataclass
class Gate:
    session: Session
    num: int

    @property
    def threshhold(self) -> FaderSetting:
        """
        The threshold for the gate for this input.
        """
        return FaderSetting(self.session, key=f"i.{self.num}.gate.thresh")

    @property
    def hold(self) -> FaderSetting:
        """
        0..1  (0 means 0ms, 1 means 2000ms).
        """
        return FaderSetting(self.session, key=f"i.{self.num}.gate.hold")

    @property
    def release(self) -> FaderSetting:
        """
        0..1  (0 means 5ms, 1 means 2000ms).
        """
        return FaderSetting(self.session, key=f"i.{self.num}.gate.release")

    @property
    def depth(self) -> FaderSetting:
        """
        0..1  (0 means -inf dB, 1 means 0dB).
        """
        return FaderSetting(self.session, key=f"i.{self.num}.gate.depth")

    @property
    def bypass(self) -> BoolSetting:
        """
        Gate bypass.
        """
        return BoolSetting(self.session, key=f"i.{self.num}.gate.bypass")


@dataclass
class Compressor:
    session: Session
    num: int

    @property
    def threshold(self) -> FaderSetting:
        """
        The threshold for the compressor for this input.
        default=0.875
        """
        return FaderSetting(self.session, key=f"i.{self.num}.dyn.threshold")

    @property
    def ratio(self) -> FaderSetting:
        """
        Compression ratio.
        default=1 (0dB).
        """
        return FaderSetting(self.session, key=f"i.{self.num}.dyn.ratio")

    @property
    def release(self) -> FaderSetting:
        """
        Compressor release time. Between 0 and 1.
        0 means 10ms, 1 means 2sec.
        default=0.48876
        """
        return FaderSetting(self.session, key=f"i.{self.num}.dyn.release")

    @property
    def attack(self) -> FaderSetting:
        """
        Compressor attack time.
        0 means 1ms, 1 means 400ms.
        """
        return FaderSetting(self.session, key=f"i.{self.num}.dyn.attack")

    @property
    def gain(self) -> FaderSetting:
        """
        Compressor output gain.
        default=0.33333333
        """
        return FaderSetting(self.session, key=f"i.{self.num}.dyn.outgain")

    @property
    def softknee(self) -> BoolSetting:
        """
        Softknee/hardknee.
        default=off.
        """
        return BoolSetting(self.session, key=f"i.{self.num}.dyn.softknee")

    @property
    def bypass(self) -> BoolSetting:
        """
        Bypass compressor.
        default=off.
        """
        return BoolSetting(self.session, key=f"i.{self.num}.dyn.bypass")


@dataclass(frozen=True, eq=True)
class InputVUMeterValues:
    pre: int = 0
    post: int = 0
    post_fader: int = 0
    gate_in: int = 0
    comp_out: int = 0
    comp_meter: int = 0


@dataclass
class VUMeter(Generic[_T]):
    _stream: ValueStream[_T]

    def subscribe(self) -> AsyncContextManager[AsyncGenerator[_T, None]]:
        """
        Subscribe to the VU meter output.

        Usage::

            async for values in vu_meter.subscribe():
                print(values.post_fader)
        """
        return self._stream.subscribe(session_id=None)


class UnknownSettingValueError(Exception):
    "Raised when a setting is retrieved for which we don't know the value."
