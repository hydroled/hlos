from .nanowebapi import Nanoweb, send_file, HttpError, cou_req
import json
import os
from lib.kernel import Service, load
from ubinascii import a2b_base64 as base64_decode
import time
import gc
import uasyncio as asyncio
import uos

CREDENTIALS = ['admin', '123456789']

content_type = {
    'js': 'text/javascript', 'svg': 'image/svg+xml', 'json': 'application/json',
    'stream': 'application/octet-stream', 'html': 'text/html; charset=utf-8', 'css': 'text/css'
}

def authenticate(credentials_ref):
    async def fail(request):
        await request.write("HTTP/1.1 401 Unauthorized\r\n")
        await request.write('WWW-Authenticate: Basic realm="HLOS"\r\n')
        await request.write("Content-Type: text/html; charset=utf-8\r\n")
        await request.write("Content-Length: 42\r\n") 
        await request.write("Connection: close\r\n")
        await request.write("\r\n")
        await request.write("<h1>401: Требуется авторизация</h1>")

    def decorator(func):
        async def wrapper(self, request):
            header = request.headers.get('authorization', request.headers.get('Authorization'))
            if header is None: return await fail(request)
            try:
                kind, authorization = header.strip().split(' ', 1)
                if kind != "Basic": return await fail(request)
                auth_parts = base64_decode(authorization.strip()).decode('ascii').split(':')
                if list(credentials_ref) != list(auth_parts): return await fail(request)
            except Exception:
                return await fail(request)
            return await func(self, request)
        return wrapper
    return decorator

async def send_header_api(request, cnt_type='json'):
    await request.write("HTTP/1.1 200 OK\r\n")
    await request.write(f"Content-Type: {content_type[cnt_type]}\r\n")
    await request.write("access-control-allow-origin: *\r\n\r\n")

async def read_json(request):
    cl = request.headers.get('content-length', request.headers.get('Content-Length', 0))
    content_length = int(cl)
    if content_length == 0: return None
    body = b''
    while len(body) < content_length:
        chunk = await request.read(content_length - len(body))
        if not chunk: break
        body += chunk
    return json.loads(body)

def get_custom_data(requested_data):
    data = {}
    data['datetime'] = '{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}.000Z'.format(*time.localtime()[:6])
    data['currdir'] = os.getcwd()
    data['uptime'] = int(time.ticks_ms() / 1000)
    data['mem_free'] = gc.mem_free()
    try:
        ffd = uos.statvfs('/')
        data['storage_free'] = ffd[1] * ffd[3]
        data['storage_total'] = ffd[0] * ffd[2]
    except:
        data['storage_free'] = 0;
        data['storage_total'] = 0
    data['load'] = min(1, load[0])
    data['cou_req'] = cou_req[0]
    data['default_pass'] = (CREDENTIALS[1] == '123456789')
    return data

class WebServer(Service):
    web_services = []

    def __init__(self, name, kernel):
        super().__init__(name)
        self.kernel = kernel
        self.app = Nanoweb(80)
        self.app.assets_extensions += ('ico',)

        self.load_settings()

        self.app.route('/*')(self.ui)
        self.app.route('/api/data')(self.api_data)
        self.app.route('/')(self.index_page)
        self.app.route('/files')(self.files_page)
        self.app.route('/network')(self.network_page)
        self.app.route('/system')(self.system_page)
        self.app.route('/cron')(self.cron_page)
        self.app.route('/standard')(self.standard_page)
        self.app.route('/editor*')(self.editor_page)
        self.app.route('/scales')(self.scales_page)
        
        # Регистрируем оба варианта (без слеша и со слешем для калькулятора)
        self.app.route('/mixer')(self.mixer_page)
        self.app.route('/mixer/')(self.mixer_page)

    def load_settings(self):
        try:
            with open('system.json', 'r') as f:
                conf = json.load(f)
                self.name = conf.get('name', self.name)
                CREDENTIALS[0] = conf.get('login', 'admin')
                CREDENTIALS[1] = conf.get('password', '123456789')
        except OSError:
            pass

    async def render_template(self, request, pages):
        for page in pages:
            path = f'/web/{page}'
            if page == '_header.html':
                try:
                    with open(path, 'r') as f:
                        content = f.read()
                    await request.write(content.replace('{{name}}', self.name))
                except:
                    await send_file(request, path)
            else:
                await send_file(request, path)

    async def render_page(self, request, content_html):
        await request.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n\r\n")
        await self.render_template(request, ('_header.html', content_html, '_footer.html'))

    @authenticate(CREDENTIALS)
    async def index_page(self, request): await self.render_page(request, 'index.html')
    @authenticate(CREDENTIALS)
    async def files_page(self, request): await self.render_page(request, 'files.html')
    @authenticate(CREDENTIALS)
    async def network_page(self, request): await self.render_page(request, 'network.html')
    @authenticate(CREDENTIALS)
    async def system_page(self, request): await self.render_page(request, 'system.html')
    @authenticate(CREDENTIALS)
    async def cron_page(self, request): await self.render_page(request, 'cron.html')
    @authenticate(CREDENTIALS)
    async def standard_page(self, request): await self.render_page(request, 'standard.html')
    @authenticate(CREDENTIALS)
    async def editor_page(self, request): await self.render_page(request, 'editor.html')
    @authenticate(CREDENTIALS)
    async def scales_page(self, request): await self.render_page(request, 'scales.html')

    @authenticate(CREDENTIALS)
    async def mixer_page(self, request):
        url_str = request.url.decode('utf-8') if isinstance(request.url, bytes) else request.url
        
        # ДЕЛЕГИРОВАНИЕ ПРИКЛАДНОМУ ОБЪЕКТУ:
        if '?' in url_str:
            app_obj = getattr(self, 'mixer_svc', None)
            if app_obj and hasattr(app_obj, 'process_request'):
                if app_obj.process_request(url_str):
                    # Если объект принял параметры, редиректим на чистый адрес
                    await request.write(b"HTTP/1.1 302 Found\r\nLocation: /mixer\r\n\r\n")
                    return

        await self.render_page(request, 'mixer.html')

    async def api_data(self, request):
        if request.method == "OPTIONS": return await self.api_send_response(request)
        data = await read_json(request)
        res = get_custom_data(data)
        res['name'] = self.name
        await send_header_api(request)
        await request.write(json.dumps(res))

    @authenticate(CREDENTIALS)
    async def ui(self, request):
        url_str = request.url.decode('utf-8') if isinstance(request.url, bytes) else request.url
        url = url_str.split('?', 1)[0]

        # --- УНИВЕРСАЛЬНЫЙ РОУТИНГ С ПАРАМЕТРАМИ ---
        # Если в адресе есть `?`, находим, какому приложению принадлежит базовый путь
        if '?' in url_str:
            for route_path, handler in self.app.routes:
                if route_path == url:
                    return await handler(request)
        # -------------------------------------------

        if url.endswith('/'): url += 'index.html'
        if '.' not in url: url += '.html'

        ext = url.split('.')[-1]
        ct = content_type.get(ext, 'text/plain')

        try:
            await request.write(f"HTTP/1.1 200 OK\r\nContent-Type: {ct}\r\nConnection: close\r\n\r\n")
            await send_file(request, self.app.STATIC_DIR + url, binary=True)
        except:
            return 'Not Found', 404

    async def api_send_response(self, request, methods="GET, POST, PUT, DELETE, OPTIONS", data=None):
        gc.collect()
        await request.write(f"HTTP/1.1 200 OK\r\naccess-control-allow-origin: *\r\n")
        await request.write("Content-Type: application/json\r\n\r\n")
        if data:
            await request.write(json.dumps(data))
        else:
            await request.write('{"status": true}')

    async def run(self):
        await asyncio.sleep(2)
        await self.app.run()

    def get_status(self):
        return {"routes": [i[0] for i in self.app.routes], "port": self.app.port}