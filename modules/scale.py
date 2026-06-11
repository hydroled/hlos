import uasyncio as asyncio
import machine
from machine import Pin
import math

class MedianFilter:
    def __init__(self, window_size=5):
        self.window_size = window_size
        self.buffer = []

    def update(self, value):
        self.buffer.append(value)
        if len(self.buffer) > self.window_size:
            self.buffer.pop(0)
            
        sorted_buf = sorted(self.buffer)
        mid = len(sorted_buf) // 2
        if len(sorted_buf) % 2 == 0 and len(sorted_buf) > 0:
            return (sorted_buf[mid - 1] + sorted_buf[mid]) / 2.0
        else:
            return sorted_buf[mid]

class Kalman:
    def __init__(self, err_measure, err_estimate, q):
        self.err_measure = err_measure
        self.err_estimate = err_estimate
        self.q = q
        self.last_estimate = 0.0

    def update(self, measurement):
        gain = self.err_estimate / (self.err_estimate + self.err_measure)
        current_estimate = self.last_estimate + gain * (measurement - self.last_estimate)
        
        self.err_estimate = (1.0 - gain) * self.err_estimate + math.fabs(self.last_estimate - current_estimate) * self.q
        self.last_estimate = current_estimate
        
        return self.last_estimate

class AsyncHX711:
    def __init__(self, dout_pin, pd_sck_pin, gain=128):
        self.dout = Pin(dout_pin, Pin.IN)
        self.pdsck = Pin(pd_sck_pin, Pin.OUT, value=0)
        
        self.OFFSET = 0
        self.SCALE = 1
        
        if gain == 128:
            self.GAIN = 1
        elif gain == 64:
            self.GAIN = 3
        elif gain == 32:
            self.GAIN = 2
        else:
            self.GAIN = 1

        self.sleep_interval = 95
        
        # Настройки детектора аномалий
        self.last_valid_raw = None
        self.max_allowed_raw_delta = 40000 
        self.spike_counter = 0
        self.max_spikes = 3

        # Кэшируем методы работы с пинами для максимальной скорости (важно для MicroPython)
        self._pdsck_on = self.pdsck.on
        self._pdsck_off = self.pdsck.off
        self._dout_val = self.dout.value

    async def read(self):
        for attempt in range(5):
            # 1. Защита от зависания при обрыве провода тензодатчика
            timeout = 0
            while self._dout_val() == 1:
                await asyncio.sleep_ms(2)
                timeout += 1
                if timeout > 100: # Максимум 200 мс ожидания
                    break
                    
            if self._dout_val() == 1:
                # Датчик так и не ответил, отдаем старое значение
                return self.last_valid_raw if self.last_valid_raw else 0

            # 2. Оптимизированная вычитка.
            # Блокируем прерывания ТОЛЬКО на микросекунды вокруг HIGH импульса!
            data = 0
            for _ in range(24):
                irq_state = machine.disable_irq()
                self._pdsck_on()
                bit = self._dout_val()
                self._pdsck_off()
                machine.enable_irq(irq_state)
                data = (data << 1) | bit

            for _ in range(self.GAIN):
                irq_state = machine.disable_irq()
                self._pdsck_on()
                self._pdsck_off()
                machine.enable_irq(irq_state)

            if data & 0x800000:
                data -= 0x1000000
                
            # Игнорируем аппаратные сбои
            if data == 8388607 or data == -8388608 or data == -1:
                await asyncio.sleep_ms(self.sleep_interval)
                continue

            # 3. Детектор аномалий (Slew Rate Limit)
            if self.last_valid_raw is not None:
                delta = abs(data - self.last_valid_raw)
                if delta > self.max_allowed_raw_delta:
                    self.spike_counter += 1
                    if self.spike_counter < self.max_spikes:
                        await asyncio.sleep_ms(self.sleep_interval)
                        continue 
                else:
                    self.spike_counter = 0

            self.spike_counter = 0
            self.last_valid_raw = data
            return data
            
        return self.last_valid_raw if self.last_valid_raw else 0

    async def read_average(self, times=3):
        if times < 1:
            raise ValueError("times must be >= 1")
        sum_val = 0
        for _ in range(times):
            sum_val += await self.read()
        return sum_val / times

    async def get_value(self, times=3):
        val = await self.read_average(times)
        return val - self.OFFSET

    async def get_units(self, times=3):
        val = await self.get_value(times)
        return val / self.SCALE

    async def tare(self, times=10):
        sum_val = await self.read_average(times)
        self.OFFSET = sum_val

    def set_scale(self, scale):
        self.SCALE = scale