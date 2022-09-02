import asyncio

from ui24r_client import Ui24RClient

__all__ = ["main"]


async def main() -> None:
    async with Ui24RClient.create("10.29.0.99") as client:
        input0 = client.input(3)
        print("Input 0 is muted: ", await input0.mute.get())
        print("Name of input 0:", await input0.name.get())
        await input0.name.set("MIC SELF")

        return

        while True:
            await input0.compressor.softknee.set(True)
            await asyncio.sleep(1)
            await input0.compressor.softknee.set(False)
            await asyncio.sleep(1)

        # await input0.gate.depth.set(.7)
        # await asyncio.sleep(1)
        return


asyncio.run(main())
