"""
Example of moving a fader up/down in a loop.
"""
from asyncio import run, sleep

from ui24r_client import Ui24RClient

__all__ = ["main"]


async def main() -> None:
    async with Ui24RClient.create("10.29.0.99") as client:
        channel = client.input(3)

        while True:
            for i in range(11):
                await channel.fader.set(i / 10)
                await sleep(0.5)

        # Panning left/right would be done like this:

        # while True:
        #     for i in range(11):
        #         await channel.pan.set(i / 10)
        #         await sleep(0.1)


run(main())
