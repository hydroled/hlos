import ujson as json
import uos
from .nanowebapi import HttpError, send_file
from .webserver import read_json, authenticate, CREDENTIALS

PROFILE_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Рецепты NPK</title>
</head>
<body style="padding: 20px; background: #f4f6f9; font-family: sans-serif;">
    <h2 style="color: #333; margin-bottom: 20px; text-align: center;">Мои рецепты</h2>
    <div style="margin-bottom: 30px; text-align: center;">
        <a href="/mixer" style="display: inline-block; padding: 10px 20px; background: #6c757d; color: white; text-decoration: none; border-radius: 5px; font-weight: bold;">← Назад к миксеру</a>
    </div>
"""

class MixerApi():
    def __init__(self, name, web, mixer_svc):
        web.web_services.append(self.__class__.__name__)
        self.web = web
        self.mixer = mixer_svc 
        
        self.web.app.route('/mixer')(self.mixer_ui_page)
        self.web.app.route('/mixer/')(self.mixer_ui_page)
        
        self.web.app.route('/profiles.html')(self.profiles_page)
        
        self.web.app.route('/api/mixer/status')(self.api_status)
        self.web.app.route('/api/mixer/start_manual')(self.api_start_manual)
        self.web.app.route('/api/mixer/stop')(self.api_stop)
        self.web.app.route('/api/mixer/tare')(self.api_tare)
        self.web.app.route('/api/mixer/clear_loaded')(self.api_clear_loaded)
        self.web.app.route('/api/mixer/flush')(self.api_flush)
        self.web.app.route('/api/mixer/update_stock')(self.api_update_stock)
        self.web.app.route('/api/mixer/save_profile')(self.api_save_profile)

    @authenticate(CREDENTIALS)
    async def mixer_ui_page(self, request):
        url_str = request.url.decode('utf-8') if isinstance(request.url, bytes) else request.url
        
        if '?' in url_str:
            if self.mixer.process_request(url_str):
                await request.write(b"HTTP/1.1 302 Found\r\nLocation: /mixer\r\n\r\n")
                return

        await self.web.render_page(request, 'mixer.html')

    @authenticate(CREDENTIALS)
    async def profiles_page(self, request):
        filepath = self.web.app.STATIC_DIR + '/profiles.html'
        
        try:
            uos.stat(filepath)
        except OSError:
            try:
                with open(filepath, 'w') as f:
                    f.write(PROFILE_TEMPLATE + "</body></html>")
            except Exception:
                pass
                
        await request.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n")
        await send_file(request, filepath, binary=True)

    async def api_save_profile(self, request):
        if request.method == "OPTIONS": return await self.web.api_send_response(request)
        data = await read_json(request)
        if not data or "name" not in data or "url" not in data:
            raise HttpError(request, 400, "Bad Request")
            
        filepath = self.web.app.STATIC_DIR + '/profiles.html'
        file_exists = True
        try:
            uos.stat(filepath)
        except OSError:
            file_exists = False

        _url = data.get("url", "")
        _name = data.get("name", "")
        
        # Генерация аккуратного абзаца (карточки) с текстом и небольшой кнопкой загрузки
        html_line = (
            '<div style="background: #fff; padding: 15px; border-radius: 8px; '
            'box-shadow: 0 2px 4px rgba(0,0,0,0.05); margin-bottom: 15px; border: 1px solid #dee2e6;">'
            '<p style="margin: 0 0 10px 0; font-size: 1.1rem; color: #333; font-weight: bold;">{}</p>'
            '<a href="/mixer/{}" style="display: inline-block; padding: 8px 16px; '
            'background: #198754; color: white; text-decoration: none; border-radius: 5px; '
            'font-size: 0.9rem; font-weight: bold;">📥 Загрузить</a>'
            '</div>\n'
        ).format(_name, _url)

        try:
            with open(filepath, 'a' if file_exists else 'w') as f:
                if not file_exists:
                    f.write(PROFILE_TEMPLATE)
                f.write(html_line)
        except Exception as e:
            raise HttpError(request, 500, str(e))
            
        await self.web.api_send_response(request, data={"status": "ok"})

    async def api_status(self, request):
        if request.method == "OPTIONS": return await self.web.api_send_response(request)
        await self.web.api_send_response(request, data=self.mixer.get_status())

    async def api_tare(self, request):
        if request.method == "OPTIONS": return await self.web.api_send_response(request)
        await self.mixer.do_tare()
        await self.web.api_send_response(request, data={"status": "ok"})

    async def api_clear_loaded(self, request):
        if request.method == "OPTIONS": return await self.web.api_send_response(request)
        self.mixer.loaded_pumps = {}
        await self.web.api_send_response(request, data={"status": "ok"})

    async def api_flush(self, request):
        if request.method == "OPTIONS": return await self.web.api_send_response(request)
        self.mixer.flush_cups()
        await self.web.api_send_response(request, data={"status": "ok"})

    async def api_update_stock(self, request):
        if request.method == "OPTIONS": return await self.web.api_send_response(request)
        data = await read_json(request)
        for k, v in data.items():
            if k in self.mixer.mixer_config["pumps"]:
                self.mixer.mixer_config["pumps"][k]["stock_g"] = float(v)
        self.mixer.save_mixer_config()
        await self.web.api_send_response(request, data={"status": "ok"})

    async def api_start_manual(self, request):
        if request.method == "OPTIONS": return await self.web.api_send_response(request)
        data = await read_json(request)
        if not data: raise HttpError(request, 400, "Данные формы пусты")
            
        self.mixer.task_queue.clear()
        pumps_conf = self.mixer.mixer_config.get("pumps", {})
        precision = float(data.get("precision", 0.1))
        
        for k, v in data.items():
            if k == "precision": continue
            val = float(v)
            if val > 0 and k in pumps_conf:
                meta = pumps_conf[k]
                self.mixer.task_queue.append({
                    "pump_id": k, "name": meta.get("name", k), 
                    "fwd": meta.get("fwd", 0), "rev": meta.get("rev", 8),
                    "cup": meta.get("cup", "A"), "amount_g": val,
                    "precision": precision
                })
        await self.web.api_send_response(request, data={"status": "ok"})

    async def api_stop(self, request):
        if request.method == "OPTIONS": return await self.web.api_send_response(request)
        self.mixer.emergency_stop()
        await self.web.api_send_response(request, data={"status": "stopped"})