"""
Print the fader values for all channels.
"""
import asyncio

from ui24r_client import Ui24RClient

__all__ = ["main"]


async def main() -> None:
    async with Ui24RClient.create("10.29.0.99") as client:
        for num in range(1, 21):
            channel = client.input(num)
            print(num, await channel.fader.get())


asyncio.run(main())
