import ujson as json
import uasyncio as asyncio
from .nanowebapi import HttpError
from .webserver import read_json

class ScalesApi():
    def __init__(self, name, web, mixer_svc):
        web.web_services.append(self.__class__.__name__)
        self.web = web
        self.mixer = mixer_svc 
        
        self.web.app.route('/api/scales/live')(self.api_live)
        self.web.app.route('/api/scales/tare')(self.api_tare)
        self.web.app.route('/api/scales/config')(self.api_config)

    async def api_live(self, request):
        if request.method == "OPTIONS": return await self.web.api_send_response(request)
        
        # Если миксер льет — выдаем флаг жесткой блокировки интерфейса
        is_locked = False
        if self.mixer and self.mixer.is_dosing:
            is_locked = True
            
        data = {"locked": is_locked, "weight": 0.0, "raw": 0, "kalman": 0}
        
        # Берем данные прямо из памяти запущенной службы миксера
        if not is_locked and self.mixer:
            data["weight"] = round(self.mixer.weight_center, 2)
            data["raw"] = int(self.mixer.weight_raw)
            data["kalman"] = int(self.mixer.weight_filtered)
            
        await self.web.api_send_response(request, data=data)

    async def api_tare(self, request):
        if request.method == "OPTIONS": return await self.web.api_send_response(request)
        if self.mixer and self.mixer.is_dosing:
            raise HttpError(request, 400, "БЛОКИРОВКА: Миксер в работе!")
            
        if self.mixer:
            # Вызываем безопасный метод тары, который сам приостановит фоновый опрос
            await self.mixer.do_tare()
        await self.web.api_send_response(request, data={"status": "ok"})

    async def api_config(self, request):
        if request.method == "OPTIONS": return await self.web.api_send_response(request)
        
        # Отдаем текущие настройки
        if request.method == "GET":
            try:
                with open('scales.json', 'r') as f:
                    conf = json.load(f)
            except Exception:
                conf = {}
            return await self.web.api_send_response(request, data=conf)
            
        # Сохраняем новые настройки и перезаряжаем миксер
        if request.method == "POST":
            if self.mixer and self.mixer.is_dosing:
                raise HttpError(request, 400, "БЛОКИРОВКА: Миксер в работе!")
                
            data = await read_json(request)
            if not data: raise HttpError(request, 400, "Нет данных")
            
            try:
                with open('scales.json', 'w') as f:
                    json.dump(data, f)
                if self.mixer:
                    self.mixer.reload_scale_config()
            except Exception as e:
                raise HttpError(request, 500, str(e))
            return await self.web.api_send_response(request, data={"status": "ok"})