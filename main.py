import gc
import machine as m
from machine import reset, Pin
import ujson as json
import webrepl

from lib.kernel import os_kernel, Kernel, Service, load
from modules.net_manager import NetworkManager
from modules.GPIO_board import GPIO_board
from modules.hldevs import PumpOnGPIO
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

webrepl.start()

net = None
cron = None
pins = None
sw = None

h = "reset(), net.sta.scan(), net.connect(lan,psw), net.status, ..."


class init():
    global net, sw, cron, pins

    # 1. Загрузка имени системы
    try:
        with open('system.json', 'r') as f:
            system_config = json.load(f)
            system_name = system_config.get('name', 'MyDevice')
            tz_offset = system_config.get('timezone', 7)
    except (OSError, ValueError):
        system_name = 'MyDevice'
        tz_offset = 7

    # 2. БЕЗОПАСНАЯ загрузка конфигурации железа
    try:
        with open('hardware.json', 'r') as f:
            hw_config = json.load(f)
    except (OSError, ValueError):
        print("ВНИМАНИЕ: Ошибка чтения hardware.json! Загружен безопасный режим.")
        hw_config = {"pins": [], "cron_commands": []}

    # Подготовка списка пинов (заменяем 1 на Pin.OUT)
    pins_list = []
    for p in hw_config.get('pins', []):
        mode = Pin.OUT if p[1] == 1 else Pin.IN
        pins_list.append((p[0], mode, p[2]))

    # --- СИСТЕМНЫЕ СЛУЖБЫ ---
    net = NetworkManager(name='NET_MANAGER', timezone_offset=tz_offset)
    os_kernel.add_task(net)

    # --- ЗАПУСК MQTT ---
    try:
        # Пытаемся прочитать конфиг. Если файла нет или JSON кривой - вылетит исключение
        with open('mqtt.json', 'r') as f:
            json.load(f)
        mqtt = SimpleMQTT(name="MQTT_Client", net_manager=net)
        os_kernel.add_task(mqtt)
        print("MQTT конфиг найден, служба MQTT_Client добавлена.")
    except (OSError, ValueError):
        print("ВНИМАНИЕ: mqtt.json не найден или поврежден. Служба MQTT отключена.")

    # Инициализация GPIO из конфига
    pins = GPIO_board(pins_list, name="GPIO_board", group=2)
    os_kernel.add_task(pins)

    cron = CronScheduler()
    os_kernel.add_task(cron)

    hw_reset = HardResetButton(name="HW_Reset")
    os_kernel.add_task(hw_reset)

    web = WebServer(name=system_name, kernel=os_kernel)
    os_kernel.add_task(web)

    pumps = PumpOnGPIO()

    # --- РЕЕСТР ОБЪЕКТОВ ДЛЯ ПЛАНИРОВЩИКА ---
    # Сюда добавляем все объекты, чьи функции можно вызывать из JSON
    cron_registry = {
        "pins": pins,
        "pumps": pumps
    }

    # --- ДИНАМИЧЕСКАЯ РЕГИСТРАЦИЯ КОМАНД КРОНА ---
    for cmd in hw_config.get('cron_commands', []):
        target_str = cmd.get('target')  # например, "pins.set_value"
        if not target_str:
            continue

        try:
            # Разбиваем "pins.set_value" на "pins" и "set_value"
            obj_name, method_name = target_str.split('.')

            if obj_name in cron_registry:
                target_obj = cron_registry[obj_name]

                # Магия Python: достаем реальную функцию по имени строки
                target_func = getattr(target_obj, method_name)

                # Регистрируем в планировщике
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

    # Запуск ядра
    os_kernel.start()


if __name__ == "__main__":
    init()
    print('System started.')
    print('Type h for help')
    print('Free RAM: ', gc.mem_free())