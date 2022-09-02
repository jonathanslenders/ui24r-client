from __future__ import annotations

from contextlib import asynccontextmanager
from enum import Enum
from typing import TYPE_CHECKING, AsyncGenerator, Callable, Generic, Type, TypeVar

if TYPE_CHECKING:
    from .client import Session

__all__ = [
    "SettingType",
    "Setting",
    "FloatSetting",
    "StrSetting",
    "FaderSetting",
    "BoolSetting",
]

_T = TypeVar("_T")


class SettingType(Enum):
    SETD = "SETD"
    SETS = "SETS"


class Setting(Generic[_T]):
    def __init__(
        self,
        session: Session,
        key: str,
        type: Type[_T],
        serialize: Callable[[_T], str],
        deserialize: Callable[[str], _T],
        setting_type: SettingType = SettingType.SETD,
    ) -> None:
        self.session = session
        self.key = key
        self.type = type
        self.serialize = serialize
        self.deserialize = deserialize
        self.setting_type = setting_type

    async def get(self) -> _T:
        "Retrieve the current value."
        return self.deserialize(await self.session.get_setting(self.key))

    def get_nowait(self) -> _T:
        """
        Retrieve the current value. Raises `UnknownSettingValueError` if we
        don't have this value yet.
        """
        return self.deserialize(self.session.get_setting_nowait(self.key))

    async def set(self, value: _T) -> None:
        """
        Set a new value.
        """
        command = {
            SettingType.SETD: "SETD",
            SettingType.SETS: "SETS",
        }[self.setting_type]

        await self.session.set_key(self.key, self.serialize(value), command=command)

    @asynccontextmanager
    async def subscribe(self) -> AsyncGenerator[AsyncGenerator[_T, None], None]:
        """
        Usage::

            async with setting.subscribe() as iterator:
                async for value in iterator:
                    print(value)
        """
        async with self.session.subscribe(self.key) as iterator:

            async def new_generator() -> AsyncGenerator[_T, None]:
                async for item in iterator:
                    yield self.deserialize(item)

            yield new_generator()


class FloatSetting(Setting[float]):
    def __init__(self, session: Session, key: str) -> None:
        super().__init__(
            session=session,
            key=key,
            type=float,
            serialize=str,
            deserialize=float,
        )


class StrSetting(Setting[str]):
    def __init__(
        self,
        session: Session,
        key: str,
        setting_type: SettingType = SettingType.SETD,
    ) -> None:
        super().__init__(
            session=session,
            key=key,
            type=str,
            serialize=str,
            deserialize=str,
            setting_type=setting_type,
        )


class FaderSetting(Setting[float]):
    """
    Fader value setting.

    This value has to be in the 0..1 range.
    0 means -inf, 1 means 10dB.
    Something like 0.7 means 0dB.
    """

    def __init__(self, session: Session, key: str) -> None:
        def serialize(value: float) -> str:
            assert 0 <= value <= 1
            return str(value)

        super().__init__(
            session=session,
            key=key,
            type=float,
            serialize=serialize,
            deserialize=float,
        )


class BoolSetting(Setting[bool]):
    def __init__(self, session: Session, key: str) -> None:
        def serialize(value: bool) -> str:
            return "1" if value else "0"

        def deserialize(value: str) -> bool:
            if value == "0":
                return False
            if value == "1":
                return True
            raise ValueError

        super().__init__(
            session=session,
            key=key,
            type=bool,
            serialize=serialize,
            deserialize=deserialize,
        )
