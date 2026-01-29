import asyncio
import logging
import aiohttp
from pypetkitapi.client import PetKitClient
from pypetkitapi.command import DeviceCommand, FeederCommand, LBCommand, DeviceAction, LitterCommand
from dotenv import load_dotenv
import os

load_dotenv()
logging.basicConfig(level=logging.DEBUG)

async def main():
    async with aiohttp.ClientSession() as session:
        client = PetKitClient(
            username="tilde@thuryism.net",  # Your PetKit account username or id
            password=os.getenv("PETKIT_PASSWORD"),  # Your PetKit account password
            region="US",  # Your region or country code (e.g. FR, US,CN etc.)
            timezone="Europe/Paris",  # Your timezone(e.g. "Asia/Shanghai")
            session=session,
        )

        await client.get_devices_data()

        # Lists all devices and pet from account

        for key, value in client.petkit_entities.items():
            print(f"{key}: {type(value).__name__} - {value.name}")

        # Select a device
        device_id = key
        # Read devices or pet information
        print(client.petkit_entities[device_id])

        # # Send command to the devices
        # ### Example 1 : Turn on the indicator light
        # ### Device_ID, Command, Payload
        # await client.send_api_request(device_id, DeviceCommand.UPDATE_SETTING, {"lightMode": 1})

        # ### Example 2 : Feed the pet
        # ### Device_ID, Command, Payload
        # # simple hopper :
        # await client.send_api_request(device_id, FeederCommand.MANUAL_FEED, {"amount": 1})
        # # dual hopper :
        # await client.send_api_request(device_id, FeederCommand.MANUAL_FEED, {"amount1": 2})
        # # or
        # await client.send_api_request(device_id, FeederCommand.MANUAL_FEED, {"amount2": 2})

        # ### Example 3 : Start the cleaning process
        # ### Device_ID, Command, Payload
        # await client.send_api_request(device_id, LitterCommand.CONTROL_DEVICE, {DeviceAction.START: LBCommand.CLEANING})


if __name__ == "__main__":
    asyncio.run(main())