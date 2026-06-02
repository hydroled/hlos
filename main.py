import gc
import machine as m
from machine import reset, Pin
import ujson as json
import uasyncio as asyncio
import webrepl

from lib.kernel import os_kernel, Kernel, Service, load
from modules.net_manager import NetworkManager
from modules.GPIO_board import GPIO_board
from modules.hldevs import PumpOnGPIO
from modules.scale import AsyncHX711
from modules.mixer import mixer
from modules.cron import CronScheduler
from modules.hw_reset import HardResetButton
from modules.mqtt_client import SimpleMQTT

# --- ВЕБ-МОДУЛИ ---
from web.webserver import WebServer
from web.files import Files
from web.switches import SwitchesApi
from web.standard import StandardApi
from web.network import NetworkApi
from web.cron import CronApi
from web.system import SystemApi
from web.scales_api import ScalesApi
from web.mixer_api import MixerApi  # ДОБАВЛЕН API МИКСЕРА

webrepl.start()

net = None
cron = None
pins = None
sw = None

h = "reset(), net.sta.scan(), net.connect(lan,psw), net.status, ..."


class init():
    global net, sw, cron, pins, scale, mixer_svc

    try:
        with open('system.json', 'r') as f:
            system_config = json.load(f)
            system_name = system_config.get('name', 'MyDevice')
            tz_offset = system_config.get('timezone', 7)
    except (OSError, ValueError):
        system_name = 'MyDevice'
        tz_offset = 7

    try:
        with open('hardware.json', 'r') as f:
            hw_config = json.load(f)
    except (OSError, ValueError):
        print("ВНИМАНИЕ: Ошибка чтения hardware.json! Загружен безопасный режим.")
        hw_config = {"pins": [], "cron_commands": []}

    pins_list = []
    for p in hw_config.get('pins', []):
        mode = Pin.OUT if p[1] == 1 else Pin.IN
        pins_list.append((p[0], mode, p[2]))

    net = NetworkManager(name='NET_MANAGER', timezone_offset=tz_offset)
    os_kernel.add_task(net)

    try:
        with open('mqtt.json', 'r') as f:
            json.load(f)
        mqtt = SimpleMQTT(name="MQTT_Client", net_manager=net)
        os_kernel.add_task(mqtt)
        print("MQTT конфиг найден, служба MQTT_Client добавлена.")
    except (OSError, ValueError):
        print("ВНИМАНИЕ: mqtt.json не найден или поврежден. Служба MQTT отключена.")

    pins = GPIO_board(pins_list, name="GPIO_board", group=2)
    os_kernel.add_task(pins)

    cron = CronScheduler()
    os_kernel.add_task(cron)

    hw_reset = HardResetButton(name="HW_Reset")
    os_kernel.add_task(hw_reset)

    web = WebServer(name=system_name, kernel=os_kernel)
    os_kernel.add_task(web)

    pumps = PumpOnGPIO()

    # --- ИНИЦИАЛИЗАЦИЯ ВЕСОВ И МИКСЕРА ---
    scale = AsyncHX711(dout_pin=32, pd_sck_pin=33)
    
    # ИСПРАВЛЕНО: Убран аргумент pumps, чтобы избежать сбоя
    mixer_svc = mixer(name="Mixer", scale=scale)
    os_kernel.add_task(mixer_svc)

    # --- РЕЕСТР ОБЪЕКТОВ ДЛЯ ПЛАНИРОВЩИКА ---
    cron_registry = {
        "pins": pins,
        "pumps": pumps,
        "scale": scale,
        "mixer": mixer_svc
    }

    for cmd in hw_config.get('cron_commands', []):
        target_str = cmd.get('target') 
        if not target_str:
            continue
        try:
            obj_name, method_name = target_str.split('.')
            if obj_name in cron_registry:
                target_obj = cron_registry[obj_name]
                target_func = getattr(target_obj, method_name)
                cron.append_command(cmd['id'], target_func, cmd['name'], cmd['args'])
            else:
                print(f"ВНИМАНИЕ: Объект '{obj_name}' не найден в реестре Крона.")
        except Exception as e:
            print(f"ВНИМАНИЕ: Ошибка загрузки задачи Крона '{target_str}': {e}")

    # --- Инициализация Веб-API ---
    _ = CronApi(name="Web cron", web=web)
    _ = Files(name="Web file manager", web=web)
    _ = SwitchesApi(name="Web switches", web=web)
    _ = StandardApi(name="Web standard", web=web)
    _ = NetworkApi(name="Network API", web=web)
    _ = SystemApi(name="System API", web=web)
    _ = ScalesApi(name="Web Scales", web=web, mixer_svc=mixer_svc)
    
    # ИСПРАВЛЕНО: Запуск API Миксера (обязательно для работы кнопок на фронтенде)
    _ = MixerApi(name="Web Mixer", web=web, mixer_svc=mixer_svc)

    os_kernel.start()

if __name__ == "__main__":
    init()
    print('System started.')
    print('Type h for help')
    print('Free RAM: ', gc.mem_free())