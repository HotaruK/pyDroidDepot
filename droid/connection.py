"""
DroidConnection is a BLE class representing a connection to a SWGE DroidDepot droid. 
It includes methods for connecting, disconnecting, sending commands, and running scripts on the droid.

It also includes instances of DroidAudioController, DroidMotorController, and DroidScriptEngine 
to manage the droid's audio, motor, and script functions.

The class only takes one argument, the "profile" string, which is the BLE profile UUID to connect to. 
Once connected, "send_droid_command" sends commands to the droid, and "execute_script" runs pre-defined scripts. 
Use "disconnect" to end the connection.

This class is licensed under MIT.
"""

import asyncio
import logging
from time import sleep
from threading import Thread
from bleak import BleakScanner, BleakClient, BleakError, AdvertisementData
from droid.protocol import *
from droid.audio import DroidAudioController
from droid.motor import DroidMotorController
from droid.script import DroidScriptEngine, DroidScriptActions, DroidScripts
from droid.hardware import DisneyManufacturerId, DroidPersonalityIdentifier, DroidAffiliation

class DroidConnection(object):
    """
    Represents a connection to a SWGE DroidDepot droid.

    Args:
        profile (str): A string representing the UUID of the BLE profile to connect to.
        manufacturer_data (dict): A dictionary containing the manufacturer data of the droid being connected.
    """

    def __init__(self, profile: str, manufacturer_data):
        """
        Initializes a new instance of the Droid class.

        Args:
            profile (str): A string representing the UUID of the BLE profile to connect to.
            manufacturer_data (dict): A dictionary containing the manufacturer data of the droid being connected.

        Attributes:
            droid: A BLE connection object for the droid.
            personality_id: The personality ID of the droid. Default is DroidPersonalityIdentifier.RUnit.
            affiliation_id: The affiliation ID of the droid. Default is DroidAffiliation.Scoundrel.
            audio_controller: An instance of the DroidAudioController class.
            script_engine: An instance of the DroidScriptEngine class.
            motor_controller: An instance of the DroidMotorController class.
            heartbeat_loop: An asyncio event loop used for the heartbeat thread.
            heartbeat_thread: A thread that runs the heartbeat_loop.
        """
        
        self.profile = profile
        self.droid = None
        self.manufacturer_data = manufacturer_data
        self.personality_id = DroidPersonalityIdentifier.RUnit
        self.affilliation_id = DroidAffiliation.Scoundrel

        self.audio_controller = DroidAudioController(self)
        self.script_engine = DroidScriptEngine(self)
        self.motor_controller = DroidMotorController(self)

        self.heartbeat_loop = asyncio.new_event_loop()
        self.heartbeat_thread = None

    async def connect(self, silent: bool = False) -> None:
        """
        Connect to the Droid using BLE.
        """

        timeout = 0.0
        self.droid = BleakClient(self.profile)
        await self.droid.connect()

        while not self.droid.is_connected and timeout < 10:
            sleep (.1)
            timeout += .1

        connect_code = bytearray.fromhex("222001")
        await self.droid.write_gatt_char(0x000d, connect_code, False)
        await self.droid.write_gatt_char(0x000d, connect_code, False)

        droid_data = self.manufacturer_data[DisneyManufacturerId]
        droid_data_len = len(droid_data)
        self.personality_id = droid_data[droid_data_len - 1]
        self.affilliation_id = (droid_data[droid_data_len - 2] - 0x80) / 2
        
        if not silent:
            await self.script_engine.execute_script(DroidScripts.DroidPairingSequence1)
            sleep(4)

        self.heartbeat_thread = Thread(target=self.__start_heartbeat_loop, args=(self.heartbeat_loop,), daemon=True)
        self.heartbeat_thread.start()
        asyncio.run_coroutine_threadsafe(self.__send_heartbeat_command(), self.heartbeat_loop)

    def __start_heartbeat_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """
        Starts the heartbeat event loop
        """

        asyncio.set_event_loop(loop)
        loop.run_forever()

    async def __send_heartbeat_command(self) -> None:
        """
        Sends our flash pairing led command to the droid. This command is used as both a connection status indicator as well
        as keeps our connection alive.
        """

        while self.droid.is_connected:
            await self.send_droid_command(DroidCommand.FlashPairingLed, "020001ff01ff0aff00")
            sleep(10)

    async def disconnect(self, silent: bool = False) -> None:
        """
        Disconnect from the Droid.
        """

        logging.info("Disconnecting from droid")
        try:
            if not silent:
                await self.audio_controller.play_shutdown_audio()
                sleep(3)
        finally:
            await self.droid.disconnect()

            if self.heartbeat_loop != None:
                self.heartbeat_loop.stop()

    def build_droid_command(self, command_id: int, data: str) -> bytearray:
        """
        The build_droid_command function creates a bytearray that represents a command for a Droid. 
        It takes in a command_id (integer) and a data string, and returns the corresponding bytearray.

        The first byte of the bytearray represents the total length of the command in bytes. The second byte is 0x42 
        if the command id is 15, or 0x00 otherwise. The third byte is the command id itself. The fourth byte is the length 
        of the data string in bytes, plus 0x40. The remaining bytes are the data string itself, represented in hexadecimal format.

        If the data string is malformed, a ValueError is raised.

        Args:
            command_id (int): The command id to be included in the Droid command
            data (str): The data string to be included in the Droid command

        Returns:
            bytearray: The bytearray representation of the Droid command, with the given command id and data string.
        """

        data_length = len(data) // 2
        header_length = 3

        if command_id == 15:
            byte2 = 0x42
        else:
            byte2 = 0x00

        total_length = data_length + header_length
        byte1 = total_length | 0x20
        byte3 = command_id
        byte4 = data_length + 0x40

        try:
            command_bytes = bytearray([byte1, byte2, byte3, byte4])
            command_bytes.extend(bytes.fromhex(data))
        except ValueError:
            raise ValueError("Failed to pack droid command (%s) with data (%s). Data is malformed" % (command_id, data))
        
        return command_bytes

    async def send_droid_command(self, command_id: int, data: str = "") -> None:
        """
        Sends a command to the Droid, composed of a command ID and optional data.

        If the data string is malformed, a ValueError is raised.

        Args:
            command_id (int): The ID of the command to send.
            data (str): Optional data to include in the command, as a string of hexadecimal digits.
        """

        command = self.build_droid_command(command_id, data)
        await self.droid.write_gatt_char(0x000d, bytearray.fromhex(command.hex()))

    async def send_droid_multi_command(self, command_id: int, data: str = "") -> None:
        """
        Sends a multi command to the Droid, composed of a command ID and optional data.

        If the data string is malformed, a ValueError is raised.

        Args:
            command_id (int): The ID of the command to send.
            data (str): Optional data to include in the command, as a string of hexadecimal digits.
        """

        command = "44%s%s" % ("{:02d}".format(command_id), data)
        await self.send_droid_command(DroidCommand.MultipurposeCommand, command)

def find_droid(device: object, advertising_data: AdvertisementData) -> bool:
    """
    Returns True if the candidate device name is "DROID", otherwise returns False.

    Args:
        device (Bleak Device): the Bluetooth device being scanned
        advertising_data (Advertisement Data): additional data collected during the scan
    
    Returns:
        True if the candidate device name is "DROID", otherwise False
    """

    return True if advertising_data.local_name == "DROID" else False

async def discover_droid(retry: bool = False) -> DroidConnection:
    """
    Scans for nearby Bluetooth devices until a device named "DROID" is found. If retry is True, the function will
    continue scanning until it finds a device or is interrupted. If retry is False, the function will time out after a
    set period of time and return without discovering a device.

    Args:
        retry (bool): whether or not to continue scanning until a device is found or the function is interrupted

    Returns:
        a DroidConnection object representing the discovered "DROID" Bluetooth device if any. Otherwise None
    """

    discovered_droid = None
    async with BleakScanner() as scanner:      
        await scanner.start()

        droids = []
        while retry and len(droids) == 0:        
            possible_droids = scanner.discovered_devices_and_advertisement_data
            if len(possible_droids) == 0:
                await asyncio.sleep(5)

            for possible_droid_address in possible_droids:
                ble_device, advertising_data = possible_droids[possible_droid_address]
                if (ble_device.name == "DROID"):
                    droids.append((ble_device, advertising_data.manufacturer_data))

            try:      
                if len(droids) == 0:
                    if not retry:
                        logging.error("Droid discovery timed out.")
                        return
                    else:
                        logging.warning("Droid discovery timed out. Retrying...")
                        continue
            except BleakError as err:
                logging.warning("Droid discovery failed. Retrying...")
                continue

            discovered_droid = droids[0]
    
    if discovered_droid is None:
        return None

    logging.info(f"Droid successfully discovered: [ {discovered_droid[0]} ]")
    d = DroidConnection(*discovered_droid)
    return d
