"""
Example of muting/unmuting a channel in a loop.
"""
from asyncio import run, sleep

from ui24r_client import Ui24RClient

__all__ = ["main"]


async def main() -> None:
    async with Ui24RClient.create("10.29.0.99") as client:
        channel = client.input(3)

        while True:
            await channel.mute.set(True)
            await sleep(1)
            await channel.mute.set(False)
            await sleep(1)


run(main())
