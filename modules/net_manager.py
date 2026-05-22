import network
import uasyncio as asyncio
import ntptime
import json
from lib.kernel import Service
import time
import machine
import ubinascii


class NetworkManager(Service):
    def __init__(self, name, timezone_offset=0):
        super().__init__(name)
        self.sta = network.WLAN(network.STA_IF)
        self.ap = network.WLAN(network.AP_IF)
        self.timezone_offset = timezone_offset
        self.is_connecting = False

    def _get_default_ap_name(self):
        """Генерирует уникальное имя AP на основе MAC-адреса"""
        try:
            uid_hex = ubinascii.hexlify(machine.unique_id()).decode('ascii').upper()
            return f"HLOS_{uid_hex[-4:]}"
        except Exception:
            return "HLOS_Fallback"

    def load_config(self):
        """Читает конфиг, а если его нет — создает правильный дефолтный файл"""
        try:
            with open('/wifi.json', 'r') as f:
                config = json.loads(f.read())
                if isinstance(config, dict):
                    return config
        except Exception:
            pass  # Переходим к созданию дефолта

        print("[NET_MANAGER] wifi.json not found or corrupted. Creating default config...")
        default_config = {
            "sta_ssid": "",
            "sta_pass": "",
            "sta_static": False,
            "sta_ip": "",
            "sta_mask": "255.255.255.0",
            "sta_gw": "",
            "sta_dns": "8.8.8.8",
            "ap_ssid": self._get_default_ap_name(),
            "ap_pass": "123456789",
            "ap_disable": False
        }

        try:
            with open('/wifi.json', 'w') as f:
                f.write(json.dumps(default_config))
        except Exception as e:
            print("[NET_MANAGER] Error saving default wifi.json:", e)

        return default_config

    def setup_ap(self):
        if not self.ap.active():
            self.ap.active(True)

        # Конфиг теперь гарантированно содержит нужные поля
        config = self.load_config()
        ap_ssid = config.get('ap_ssid', self._get_default_ap_name())
        ap_pass = config.get('ap_pass', '123456789')

        try:
            if ap_pass and len(ap_pass) >= 8:
                self.ap.config(essid=ap_ssid, password=ap_pass, authmode=3)
            else:
                self.ap.config(essid=ap_ssid, authmode=0)
            print(f"Access Point '{ap_ssid}' is active.")
        except Exception as e:
            print(f"AP setup error: {e}")

    async def connect_to_network(self):
        if self.is_connecting: return False
        self.is_connecting = True
        try:
            config = self.load_config()
            sta_ssid = config.get('sta_ssid')
            sta_pass = config.get('sta_pass', '')

            if not sta_ssid:
                return False

            print(f"Connecting to router: {sta_ssid}")
            self.sta.active(True)
            self.sta.disconnect()
            await asyncio.sleep(1)

            if config.get('sta_static') and config.get('sta_ip'):
                try:
                    self.sta.ifconfig((
                        config.get('sta_ip'),
                        config.get('sta_mask', '255.255.255.0'),
                        config.get('sta_gw', ''),
                        config.get('sta_dns', '8.8.8.8')
                    ))
                except Exception:
                    self.sta.ifconfig('dhcp')
            else:
                self.sta.ifconfig('dhcp')

            self.sta.connect(sta_ssid, sta_pass)

            for _ in range(15):
                await asyncio.sleep(1)
                if self.sta.isconnected():
                    print('Connected! IP:', self.sta.ifconfig())
                    return True

            self.sta.disconnect()
            return False
        except Exception as e:
            print("Connection error:", e)
            return False
        finally:
            self.is_connecting = False

    async def sync_time(self):
        try:
            if not self.sta.isconnected(): return False
            print("Trying to sync NTP time...")
            ntptime.settime()
            rtc = machine.RTC()

            tz_offset = self.timezone_offset
            try:
                with open('system.json', 'r') as f:
                    sys_conf = json.loads(f.read())
                    tz_offset = int(sys_conf.get('timezone', tz_offset))
            except Exception:
                pass

            t = time.time() + (tz_offset * 3600)
            tm = time.localtime(t)
            rtc.datetime((tm[0], tm[1], tm[2], tm[6], tm[3], tm[4], tm[5], 0))

            print(f"NTP time synced with UTC {tz_offset}: ", time.localtime())
            return True
        except Exception as e:
            print("Failed to sync time:", e)
            return False

    async def monitor_network(self):
        while True:
            await asyncio.sleep(30)
            config = self.load_config()

            if config.get('sta_ssid') and not self.sta.isconnected():
                print("Connection lost. Reconnecting...")
                self.setup_ap()
                await self.connect_to_network()

            year = time.localtime()[0]
            if self.sta.isconnected() and year < 2022:
                await self.sync_time()

            ap_disable = config.get('ap_disable', False)
            has_internet = self.sta.isconnected() and time.localtime()[0] >= 2022

            if ap_disable and has_internet:
                if self.ap.active():
                    print("Internet OK. Disabling AP to save power.")
                    self.ap.active(False)
            else:
                if not self.ap.active():
                    print("No internet or AP always-on mode. Activating AP.")
                    self.setup_ap()

    async def run(self):
        self.setup_ap()
        await asyncio.sleep(1)

        await self.connect_to_network()
        if self.sta.isconnected():
            await self.sync_time()

        asyncio.create_task(self.monitor_network())

    def connect(self, *args, save=True):
        pass

    def forget(self):
        pass

    def create_access_point(self):
        self.setup_ap()

    def get_status(self):
        return {"connected": self.sta.isconnected()}

    async def scan_networks(self):
        return []