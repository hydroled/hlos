import uasyncio as asyncio
import uerrno
import json

__version__ = '1.0.0'

http_status = {200: "200 OK", 401: '401 Unauthorized', 404: "404 Not Found",
               500: "500 Internal error", 501: "501 Not Implemented", 505: "505 Version Not Supported"}

cou_req = [0]


class HttpError(Exception): pass


class Request:
    def __init__(self):
        self.url = "";
        self.method = "";
        self.headers = {};
        self.route = ""
        self.read = None;
        self.write = None;
        self.close = None


class EventData():
    def __init__(self, data): self.data = data


async def write(request, data):
    await request.write(data.encode('ISO-8859-1') if type(data) == str else data)


async def error(request, code, reason):
    await request.write("HTTP/1.1 %s %s\r\n\r\n<h1>%s</h1>" % (code, reason, reason))
    print('request_error: ', code, request.url)


async def send_file(request, filename, segment=64, binary=False):
    try:
        print('NW: ', filename)
        with open(filename, 'rb' if binary else 'r') as f:
            while True:
                data = f.read(segment)
                if not data: break
                await request.write(data)
    except OSError as e:
        if e.args[0] != uerrno.ENOENT: raise
        raise HttpError(request, 404, f"File '{filename}' Not Found")


class Nanoweb:
    # ИСПРАВЛЕНИЕ: Добавлены ключи в нижнем регистре
    extract_headers = ('Authorization', 'Content-Length', 'Content-Type', 'authorization', 'content-length',
                       'content-type')

    def __init__(self, port=80, address='0.0.0.0'):
        self.port = port
        self.address = address
        self.headers = {}
        self.routes = []
        self.assets_extensions = ('html', 'css', 'js')
        self.callback_request = None
        self.callback_error = staticmethod(error)
        self.STATIC_DIR = '/web'
        self.INDEX_FILE = self.STATIC_DIR + 'index.html'

    def route(self, route, index=0):
        def decorator(func):
            self.routes.insert(0, (route, func,))
            return func

        return decorator

    async def send_resp(self, request, data, status=200):
        await request.write(f"HTTP/1.1 {http_status[status]}\r\naccess-control-allow-origin: *\r\n")
        if isinstance(data, str):
            await request.write("Content-Type: text/html\r\n\r\n" + data)
        elif isinstance(data, dict):
            await request.write("Content-Type: application/json\r\n\r\n" + json.dumps(data))
        await asyncio.sleep(1)

    async def generate_output(self, request, handler):
        while True:
            if isinstance(handler, str) or isinstance(handler, dict):
                await self.send_resp(request, handler)
            elif isinstance(handler, tuple):
                status = len(handler) > 1 and handler[1] or 200
                await self.send_resp(request, handler[0], status)
            else:
                handler = await handler(request)
                if handler: continue
            break

    async def handle(self, reader, writer):
        # Получаем IP клиента
        peer = writer.get_extra_info('peername')
        client_ip = peer[0] if peer else "Unknown"

        if cou_req[0] > 6:
            print(f'err max-req: drop connection from {client_ip}')
            await writer.aclose()
            return

        cou_req[0] += 1

        try:
            items = await reader.readline()
            items = items.decode('ascii').split()
            if len(items) != 3: return

            request = Request()
            request.read = reader.read
            request.write = writer.awrite
            request.close = writer.aclose
            request.method, request.url, version = items

            if cou_req[0] > 5:
                print(f'warn max-req: cou_req={cou_req[0]}, IP={client_ip}, URL={request.url}')

            try:
                if version not in ("HTTP/1.0", "HTTP/1.1"): raise HttpError(request, 505, "Version Not Supported")
                while True:
                    items = await reader.readline()
                    items = items.decode('ascii').split(":", 1)
                    if len(items) == 2:
                        header, value = items
                        if header in self.extract_headers: request.headers[header] = value.strip()
                    elif len(items) == 1:
                        break

                if self.callback_request: self.callback_request(request)

                for route, handler in self.routes:
                    if route == request.url or (route[-1] == '*' and request.url.startswith(route[:-1]) and route.count(
                            '/') <= request.url.count('/')):
                        request.route = route
                        await self.generate_output(request, handler)
                        break
                else:
                    if request.url in ('', '/'):
                        await send_file(request, self.INDEX_FILE)
                    else:
                        for ext in self.assets_extensions:
                            if request.url.endswith('.' + ext):
                                await send_file(request, '%s/%s' % (self.STATIC_DIR, request.url), binary=True)
                                break
                        else:
                            raise HttpError(request, 404, "File Not Found")
            except HttpError as e:
                request, code, message = e.args
                await self.callback_error(request, code, message)
        except OSError as e:
            if e.args[0] != uerrno.ECONNRESET:
                print(f"Nanoweb OSError: {e}")
        except Exception as e:
            print(f"Nanoweb Handle Error: {e}")
        finally:
            await writer.aclose()
            cou_req[0] -= 1

    async def run(self):
        return await asyncio.start_server(self.handle, self.address, self.port)