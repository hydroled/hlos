import uasyncio as asyncio
import ujson as json
import machine
from machine import Pin, I2C
import time
from lib.kernel import Service
from modules.scale import Kalman, MedianFilter

class mixer(Service):
    def __init__(self, name="Mixer_SVC", scale=None):
        super().__init__(name)
        self.scale = scale
        self.is_dosing = False
        
        self.weight_raw = 0.0
        self.weight_filtered = 0.0
        self.weight_center = 0.0
        
        self.cup_a_filled = 0.0
        self.cup_b_filled = 0.0
        self.current_step_poured = 0.0
        
        self.loaded_pumps = {}
        self.global_precision = 0.1
        self.task_queue = []
        self.current_task = None
        self.error_msg = ""
        self.scale_busy = False

        try:
            self.driver_en1 = Pin(2, Pin.OUT, value=0)
            self.driver_en2 = Pin(12, Pin.OUT, value=0)
        except Exception:
            self.driver_en1 = None
            self.driver_en2 = None
        
        self.scale_config = self._load_json('scales.json', {
            "coeff_center": 400.0, "median_window": 3, "cups_weight_g": 0.0,
            "kalman_err_measure": 100, "kalman_err_estimate": 10, "kalman_q": 0.1,
            "hw_offset": 0.0
        })
        
        # === ВОССТАНОВЛЕНИЕ ТАРЫ ПОСЛЕ ПЕРЕЗАГРУЗКИ ===
        # Загружаем сохраненный аппаратный ноль прямо в драйвер HX711
        if self.scale and hasattr(self.scale, 'OFFSET'):
            self.scale.OFFSET = self.scale_config.get("hw_offset", 0.0)
        
        self.mixer_config = self._load_json('mixer.json', {
            "i2c_scl": 22, "i2c_sda": 21, "mcp_address": 0x20, "max_cup_weight_g": 450.0,
            "pumps": {
                "p1": {"fwd": 1, "rev": 9,  "cup": "A", "name": "1:Ca", "stock_g": 0.0},
                "p2": {"fwd": 8, "rev": 0,  "cup": "A", "name": "2:NK", "stock_g": 0.0},
                "p3": {"fwd": 3, "rev": 11, "cup": "A", "name": "3:NH", "stock_g": 0.0},
                "p4": {"fwd": 10, "rev": 2,  "cup": "A", "name": "4:MN", "stock_g": 0.0},
                "p5": {"fwd": 5, "rev": 13, "cup": "B", "name": "5:KS", "stock_g": 0.0},
                "p6": {"fwd": 12, "rev": 4,  "cup": "B", "name": "6:KP", "stock_g": 0.0},
                "p7": {"fwd": 7, "rev": 15, "cup": "B", "name": "7:Mg", "stock_g": 0.0},
                "p8": {"fwd": 14, "rev": 6,  "cup": "B", "name": "8:MK", "stock_g": 0.0}
            }
        })

        self.median_filter = MedianFilter(window_size=self.scale_config.get("median_window", 3))
        self.kalman_filter = Kalman(
            self.scale_config.get("kalman_err_measure", 100),
            self.scale_config.get("kalman_err_estimate", 10),
            self.scale_config.get("kalman_q", 0.1)
        )
        
        scl_pin = self.mixer_config.get("i2c_scl", 22)
        sda_pin = self.mixer_config.get("i2c_sda", 21)
        self.mcp_addr = self.mixer_config.get("mcp_address", 0x20)
        
        self.mcp_state = 0x0000 
        try:
            self.i2c = I2C(0, scl=Pin(scl_pin), sda=Pin(sda_pin), freq=100000)
            self.i2c.writeto_mem(self.mcp_addr, 0x0A, b'\x00')
            self.i2c.writeto_mem(self.mcp_addr, 0x00, b'\x00') 
            self.i2c.writeto_mem(self.mcp_addr, 0x01, b'\x00') 
            self._write_mcp_pumps()
        except Exception:
            self.i2c = None

    def _load_json(self, filename, default_dict):
        try:
            with open(filename, 'r') as f: return json.load(f)
        except Exception:
            try:
                with open(filename, 'w') as f: json.dump(default_dict, f)
            except Exception: pass
            return default_dict

    def save_mixer_config(self):
        try:
            with open('mixer.json', 'w') as f: json.dump(self.mixer_config, f)
        except Exception: pass

    def save_scale_config(self):
        try:
            with open('scales.json', 'w') as f: json.dump(self.scale_config, f)
        except Exception: pass

    def reload_scale_config(self):
        try:
            with open('scales.json', 'r') as f: self.scale_config = json.load(f)
        except Exception: pass

    def _write_mcp_pumps(self):
        if self.i2c:
            try:
                self.i2c.writeto_mem(self.mcp_addr, 0x14, bytes([self.mcp_state & 0xFF]))
                self.i2c.writeto_mem(self.mcp_addr, 0x15, bytes([(self.mcp_state >> 8) & 0xFF]))
            except Exception: pass

    def _set_drivers_enable(self, state):
        val = 1 if state else 0
        if self.driver_en1: self.driver_en1.value(val)
        if self.driver_en2: self.driver_en2.value(val)

    def emergency_stop(self):
        self.task_queue.clear()
        self.is_dosing = False
        self.current_task = None
        self.mcp_state = 0x0000
        self._write_mcp_pumps()
        self._set_drivers_enable(False)
        self.current_step_poured = 0.0
        if not self.error_msg or "чашек" in self.error_msg:
            self.error_msg = "Остановлено оператором"

    def flush_cups(self):
        self.cup_a_filled = 0.0
        self.cup_b_filled = 0.0
        self.current_step_poured = 0.0
        self.error_msg = ""

    async def do_tare(self):
        if not self.scale: return
        self.scale_busy = True

        # 1. Мгновенно обнуляем интерфейс, чтобы браузер не поймал фантомный старый вес
        self.weight_center = 0.0
        self.weight_filtered = 0.0
        self.weight_raw = 0.0

        await asyncio.sleep_ms(50)
        try:
            await self.scale.tare(times=10)

            # Сохранение тары во флеш
            if hasattr(self.scale, 'OFFSET'):
                self.scale_config["hw_offset"] = self.scale.OFFSET
                self.save_scale_config()

            # 2. Полный сброс буферов и ЖЕСТКОЕ пересоздание фильтра Калмана
            self.median_filter.buffer.clear()
            self.kalman_filter = Kalman(
                self.scale_config.get("kalman_err_measure", 100),
                self.scale_config.get("kalman_err_estimate", 10),
                self.scale_config.get("kalman_q", 0.1)
            )

            self.cup_a_filled = 0.0
            self.cup_b_filled = 0.0
            self.error_msg = ""
        except Exception:
            pass
        finally:
            self.scale_busy = False

    async def _get_real_weight(self):
        if not self.scale: return 0.0
            
        try:
            raw = await asyncio.wait_for(self.scale.get_value(times=1), 1.0)
        except asyncio.TimeoutError:
            return self.weight_center
        except Exception:
            return self.weight_center
            
        if raw is None: return self.weight_center
            
        self.weight_raw = raw
        med = self.median_filter.update(raw)
        filtered = self.kalman_filter.update(med)
        self.weight_filtered = filtered
        
        coeff = self.scale_config.get("coeff_center", 400.0)
        try:
            coeff = float(coeff)
            if coeff == 0: coeff = 400.0
        except (ValueError, TypeError):
            coeff = 400.0
            
        self.weight_center = filtered / coeff
        return self.weight_center

    def process_request(self, url_str):
        try:
            if '?' not in url_str: return False
            qs = url_str.split('?', 1)[1]
            params = {k: v for item in qs.split('&') if '=' in item for k, v in [item.split('=', 1)]}
            
            if 's' not in params and not any(f'p{i}' in params for i in range(1, 9)): return False
                
            if params.get('s') == '0':
                self.flush_cups()
                self.loaded_pumps = {}
                self.reload_scale_config()
                
            for i in range(1, 9):
                key = f"p{i}"
                if key in params:
                    try:
                        val = float(params[key])
                        if val > 0: self.loaded_pumps[key] = val
                    except ValueError: pass
            return True
        except Exception: return False

    async def monitor_loop(self):
        import gc
        while True:
            if not self.scale_busy:
                await self._get_real_weight()
            
            # Динамическое распределение процессорного времени
            if self.is_dosing:
                await asyncio.sleep_ms(40)
            else:
                gc.collect() 
                await asyncio.sleep_ms(250)

    async def _dosing_worker(self):
        while True:
            cups_present = self.weight_center >= -20.0

            if len(self.task_queue) > 0 and not self.is_dosing:
                if not cups_present:
                    self.error_msg = "ОШИБКА: Нет чашек на весах!"
                    await asyncio.sleep_ms(1000)
                    continue 
                
                if "чашек" in self.error_msg:
                    self.error_msg = ""
                    
                self.is_dosing = True
                self.current_task = self.task_queue.pop(0)
                try:
                    await self._execute_step(self.current_task)
                except Exception as e:
                    self.error_msg = str(e)
                    self.emergency_stop()
                finally:
                    self.current_task = None
                    self.is_dosing = False
            await asyncio.sleep_ms(300)

    async def _execute_step(self, task):
        pid = task["pump_id"]
        pin_fwd = task["fwd"]
        pin_rev = task["rev"]
        cup = task["cup"]
        target_g = float(task["amount_g"])
        precision = float(task.get("precision", 0.1)) 
        
        self.scale_busy = True
        step_timeout_err = False
        
        try:
            start_weight = self.weight_center
            target_absolute = start_weight + target_g
            max_cap = self.mixer_config.get("max_cup_weight_g", 450.0)
            
            if target_absolute > max_cap:
                raise Exception(f"Превышен лимит объема чаши {cup}!")

            self._set_drivers_enable(True) 
            await asyncio.sleep_ms(10)     
            
            start_time = time.time()
            timeout = (target_g * 5.0) + 60.0 
            
            while True:
                await self._get_real_weight()
                self.current_step_poured = max(0.0, self.weight_center - start_weight)
                remaining = target_g - self.current_step_poured
                
                if remaining <= precision:
                    break
                    
                if time.time() - start_time > timeout:
                    step_timeout_err = True
                    break
                
                if remaining > 2.0:
                    if 0 <= pin_rev <= 15: self.mcp_state &= ~(1 << pin_rev)
                    if 0 <= pin_fwd <= 15: self.mcp_state |= (1 << pin_fwd)
                    self._write_mcp_pumps()
                    await asyncio.sleep_ms(30)
                else:
                    pulse_duration = 100 if remaining > 0.5 else 40
                    
                    if 0 <= pin_rev <= 15: self.mcp_state &= ~(1 << pin_rev)
                    if 0 <= pin_fwd <= 15: self.mcp_state |= (1 << pin_fwd)
                    self._write_mcp_pumps()
                    
                    await asyncio.sleep_ms(pulse_duration)
                    
                    if 0 <= pin_fwd <= 15: self.mcp_state &= ~(1 << pin_fwd)
                    if 0 <= pin_rev <= 15: self.mcp_state &= ~(1 << pin_rev)
                    self._write_mcp_pumps()
                    
                    await asyncio.sleep_ms(1200)
                    for _ in range(4):
                        await self._get_real_weight()
                        await asyncio.sleep_ms(20)
                
        finally:
            if 0 <= pin_fwd <= 15: self.mcp_state &= ~(1 << pin_fwd)
            if 0 <= pin_rev <= 15: self.mcp_state &= ~(1 << pin_rev)
            self._write_mcp_pumps()
            self._set_drivers_enable(False) 
            self.scale_busy = False
            
        await asyncio.sleep(2)
        
        actual_poured = max(0.0, self.weight_center - start_weight)
        if cup == "A": self.cup_a_filled += actual_poured
        else: self.cup_b_filled += actual_poured
        self.current_step_poured = 0.0

        if pid in self.mixer_config["pumps"]:
            current_stock = self.mixer_config["pumps"][pid].get("stock_g", 0.0)
            new_stock = max(0.0, current_stock - actual_poured)
            self.mixer_config["pumps"][pid]["stock_g"] = round(new_stock, 1)
            self.save_mixer_config()

        if step_timeout_err:
            raise Exception(f"Таймаут налива {task['pump_id']}.")

    async def run(self):
        asyncio.create_task(self._dosing_worker())
        await self.monitor_loop()

    def get_status(self):
        live_a = self.cup_a_filled + (self.current_step_poured if (self.current_task and self.current_task["cup"] == "A") else 0.0)
        live_b = self.cup_b_filled + (self.current_step_poured if (self.current_task and self.current_task["cup"] == "B") else 0.0)
        
        cups_present = self.weight_center >= -20.0

        stocks = {k: v.get("stock_g", 0.0) for k, v in self.mixer_config.get("pumps", {}).items()}

        return {
            "is_dosing": self.is_dosing,
            "weight_total": round(self.weight_center, 2),
            "weight_a": round(live_a, 2),
            "weight_b": round(live_b, 2),
            "tasks_left": len(self.task_queue),
            "current_task": self.current_task,
            "error": self.error_msg,
            "loaded_pumps": self.loaded_pumps,
            "cups_present": cups_present,
            "stocks": stocks
        }