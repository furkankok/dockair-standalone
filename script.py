import asyncio
import json
import sys
import docker
import websockets
import logging
import os
import sentry_sdk
import time
import threading
import queue
import datetime

logging.basicConfig(level=logging.INFO, format='{asctime} - {levelname} - {message}', style='{')
logger = logging.getLogger(__name__)

# for testing
if not os.getenv("WEBSOCKET_SERVER_URL"):
    os.environ["WEBSOCKET_SERVER_URL"] = "ws://127.0.0.1:8000/ws/client/"
    os.environ["USER_TOKEN"] = "44a6ecf1-d936-4b46-983f-a28eba487707"
    os.environ["PRODUCTION"] = "false"


class DockerSystemManager:
    def __init__(self):
        self.docker_client = docker.from_env()
        self._websocket = None
        self._ws_lock = threading.Lock()
        
        self._event_queue = queue.Queue(maxsize=1000)
        
        self._ws_connected = threading.Event()
        
        self._log_listeners = {}
        self._log_listeners_lock = threading.Lock()
        
        self._stop_listening = {}
        
        self._container_last_log_times = {}
        self._last_log_times_lock = threading.Lock()
        
        self._last_sent_logs = {}
        self._last_logs_lock = threading.Lock()
        
        self._container_restart_times = {}
        
        self._running_containers = set()
        self._containers_lock = threading.Lock()
        
        self._periodic_info_running = False
        
        self._pending_events = []

    def _set_websocket(self, ws):
        """Websocket nesnesini güvenli bir şekilde ayarla"""
        with self._ws_lock:
            self._websocket = ws
            if ws:
                self._ws_connected.set()
                logger.info("WebSocket bağlantısı aktif olarak işaretlendi")
            else:
                self._ws_connected.clear()
                logger.info("WebSocket bağlantısı deaktif olarak işaretlendi")

    def _get_websocket(self):
        """Websocket nesnesini güvenli bir şekilde al"""
        with self._ws_lock:
            return self._websocket

    async def connect_to_websocket(self):
        WEBSOCKET_SERVER_URL = os.getenv("WEBSOCKET_SERVER_URL") + os.getenv("USER_TOKEN") + "/"
        
        max_reconnect_time = 60
        reconnect_interval = 1
        backoff_factor = 1.5
        current_interval = reconnect_interval

        asyncio.create_task(self._event_processor())

        while True:
            try:
                logger.info(f"WebSocket bağlantısı kuruluyor: {WEBSOCKET_SERVER_URL}")
                async with websockets.connect(WEBSOCKET_SERVER_URL) as websocket:
                    logger.info(f"WebSocket bağlantısı başarılı!")
                    
                    self._set_websocket(websocket)
                    current_interval = reconnect_interval
                    
                    if self._pending_events:
                        logger.info(f"{len(self._pending_events)} bekleyen event bulundu, gönderiliyor...")
                        pending_copy = self._pending_events.copy()
                        self._pending_events.clear()
                        
                        for event_data in pending_copy:
                            try:
                                await websocket.send(event_data)
                            except Exception as e:
                                logger.error(f"Bekleyen event gönderimi başarısız: {e}")
                                self._pending_events.append(event_data)
                                break
                    
                    try:
                        while True:
                            receive_task = asyncio.create_task(websocket.recv())
                            ping_task = asyncio.create_task(asyncio.sleep(30))
                            
                            done, pending = await asyncio.wait(
                                [receive_task, ping_task],
                                return_when=asyncio.FIRST_COMPLETED
                            )
                            
                            for task in pending:
                                task.cancel()
                            
                            if receive_task in done:
                                try:
                                    message = receive_task.result()
                                    await self._process_client_message(message)
                                except Exception as e:
                                    logger.error(f"Client mesajı işlenirken hata: {e}")
                            
                            if ping_task in done:
                                await websocket.ping()
                    except Exception as e:
                        logger.warning(f"WebSocket bağlantısı koptu: {e}")
                        self._set_websocket(None)
                        continue
                            
            except Exception as e:
                logger.error(f"WebSocket bağlantı hatası: {e}")
                self._set_websocket(None)
                
                await asyncio.sleep(current_interval)
                current_interval = min(current_interval * backoff_factor, max_reconnect_time)

    async def _process_client_message(self, message):
        """Client'tan gelen JSON mesajlarını işler ve Docker komutlarını uygular"""
        try:
            data = json.loads(message).get("data")
            
            command = data.get("command")
            container_id = data.get("container_id")
            
            if not command or not container_id:
                logger.error(f"Geçersiz komut formatı: {message}")
                self.send_event("command_result", {
                    "command": command,
                    "container_id": container_id,
                    "success": False,
                    "error": "Geçersiz komut formatı. 'command' ve 'container_id' gerekli."
                })
                return
                
            logger.info(f"Client'tan komut alındı: {command} için {container_id}")
            
            try:
                container = self.docker_client.containers.get(container_id)
                result = {"command": command, "container_id": container_id, "container_name": container.name}
                
                if command == "start_container":
                    container.start()
                    result["success"] = True
                    result["message"] = f"Container {container.name} başlatıldı"
                    logger.info(f"Container {container.name} (ID: {container_id}) başlatıldı")
                    
                elif command == "stop_container":
                    container.stop()
                    result["success"] = True
                    result["message"] = f"Container {container.name} durduruldu"
                    logger.info(f"Container {container.name} (ID: {container_id}) durduruldu")
                    
                elif command == "restart_container":
                    container.restart()
                    result["success"] = True
                    result["message"] = f"Container {container.name} yeniden başlatıldı"
                    logger.info(f"Container {container.name} (ID: {container_id}) yeniden başlatıldı")
                    
                else:
                    result["success"] = False
                    result["error"] = f"Bilinmeyen komut: {command}"
                    logger.error(f"Bilinmeyen komut alındı: {command}")
                
                self.send_event("command_result", result)
                
            except docker.errors.NotFound:
                logger.error(f"Container bulunamadı: {container_id}")
                self.send_event("command_result", {
                    "command": command,
                    "container_id": container_id,
                    "success": False,
                    "error": f"Container bulunamadı: {container_id}"
                })
                
            except Exception as e:
                logger.error(f"Docker komut hatası: {e}")
                self.send_event("command_result", {
                    "command": command,
                    "container_id": container_id,
                    "success": False,
                    "error": str(e)
                })
                
        except json.JSONDecodeError:
            logger.error(f"Geçersiz JSON formatı: {message}")
            self.send_event("command_result", {
                "success": False,
                "error": "Geçersiz JSON formatı"
            })
            
        except Exception as e:
            logger.error(f"Mesaj işleme hatası: {e}")
            self.send_event("command_result", {
                "success": False,
                "error": f"Mesaj işleme hatası: {str(e)}"
            })

    async def _event_processor(self):
        """Event queue'dan eventleri alıp websocket üzerinden gönderir"""
        while True:
            try:
                try:
                    event_data = self._event_queue.get(block=True, timeout=0.001)
                except queue.Empty:
                    await asyncio.sleep(0.001)
                    continue
                
                ws = self._get_websocket()
                if ws:
                    try:
                        await ws.send(event_data)
                        logger.debug(f"Event gönderildi: {event_data[:30]}...")
                    except Exception as e:
                        logger.error(f"Event gönderimi hatası: {e}")
                        self._pending_events.append(event_data)
                        self._set_websocket(None)  # Websocket'i deaktif olarak işaretle
                else:
                    # WebSocket bağlı değil, bekleyen eventlere ekle
                    self._pending_events.append(event_data)
                
                # Queue'dan alınan event'i işlendi olarak işaretle
                self._event_queue.task_done()
            
            except Exception as e:
                logger.error(f"Event işleme hatası: {e}")
                await asyncio.sleep(0.1)  # Herhangi bir hata durumunda kısa bir bekleme

    def send_event(self, event_type, data):
        """Event gönderme - thread-safe ve non-blocking"""
        try:
            # Event verilerini JSON'a dönüştür
            event_json = json.dumps({"event": event_type, "data": data})
            
            # Queue'ya ekle - dolar ve bloke olursa son birkaç event'i at
            try:
                self._event_queue.put_nowait(event_json)
                # logger.debug(f"Event queue'ya eklendi: {event_type}")
                return True
            except queue.Full:
                logger.warning(f"Event queue dolu, event atılıyor: {event_type}")
                # Queue dolu olduğunda en eski event'i çıkar ve yenisini ekle
                try:
                    self._event_queue.get_nowait()  # En eski event'i çıkar
                    self._event_queue.put_nowait(event_json)  # Yeni event'i ekle
                except:
                    pass  # En kötü durumda event'i atla
                return False
                
        except Exception as e:
            logger.error(f"Event oluşturma hatası: {e}")
            return False
            
    def get_docker_info(self):
        """Tüm containerların bilgilerini getir"""
        containers = self.docker_client.containers.list(all=True)
        container_list = []
        for container in containers:
            try:
                compose_project = container.labels.get("com.docker.compose.project", container.name)
                
                # Port bilgilerini al
                ports = container.ports
                
                # Eğer container çalışmıyorsa ve ports boşsa, config'den port bilgilerini al
                if not ports or container.status != "running":
                    # Exposed ports (container içindeki portlar)
                    exposed_ports = container.attrs.get("Config", {}).get("ExposedPorts", {})
                    # Port bindings (host tarafındaki mappingler)
                    port_bindings = container.attrs.get("HostConfig", {}).get("PortBindings", {})
                    
                    # Eğer running olmayan container için port bilgisi yoksa, konfigürasyon bilgilerinden oluştur
                    if not ports:
                        ports = {}
                        
                    # Port binding bilgilerini ports dict'ine ekle
                    for container_port, host_bindings in port_bindings.items():
                        if host_bindings:
                            # Standart port formatına dönüştür
                            # Örnek: "80/tcp" -> {"HostIp": "0.0.0.0", "HostPort": "8080"}
                            port_proto = container_port
                            ports[port_proto] = [{"HostIp": binding.get("HostIp", "0.0.0.0"), 
                                                 "HostPort": binding.get("HostPort", "")} 
                                                for binding in host_bindings]
                    
                    # ExposedPorts'dan gelen ve PortBindings'de olmayan portları da ekle
                    for port_proto in exposed_ports:
                        if port_proto not in ports:
                            ports[port_proto] = []
                
                container_info = {
                    "id": container.id,
                    "name": container.name,
                    "status": container.status,
                    "ports": ports,
                    "image": container.image.tags[0] if container.image.tags else "-",
                    "created": container.attrs["Created"],
                    "state": container.attrs["State"],
                    "compose_project": compose_project,
                    "last_started": container.attrs["State"]["StartedAt"],
                }
                container_list.append(container_info)
            except Exception as e:
                logger.error(f"Container bilgisi alınırken hata: {e}")
                # Hatalı container'ı atla ama diğerlerine devam et
        return container_list
            
    def get_single_container_info(self, container_id):
        """Tek bir container'ın bilgilerini getirir"""
        try:
            container = self.docker_client.containers.get(container_id)
            compose_project = container.labels.get("com.docker.compose.project", container.name)
            
            # Port bilgilerini al
            ports = container.ports
            
            # Eğer container çalışmıyorsa ve ports boşsa, config'den port bilgilerini al
            if not ports or container.status != "running":
                # Exposed ports (container içindeki portlar)
                exposed_ports = container.attrs.get("Config", {}).get("ExposedPorts", {})
                # Port bindings (host tarafındaki mappingler)
                port_bindings = container.attrs.get("HostConfig", {}).get("PortBindings", {})
                
                # Eğer running olmayan container için port bilgisi yoksa, konfigürasyon bilgilerinden oluştur
                if not ports:
                    ports = {}
                    
                # Port binding bilgilerini ports dict'ine ekle
                for container_port, host_bindings in port_bindings.items():
                    if host_bindings:
                        # Standart port formatına dönüştür
                        # Örnek: "80/tcp" -> {"HostIp": "0.0.0.0", "HostPort": "8080"}
                        port_proto = container_port
                        ports[port_proto] = [{"HostIp": binding.get("HostIp", "0.0.0.0"), 
                                             "HostPort": binding.get("HostPort", "")} 
                                            for binding in host_bindings]
                
                # ExposedPorts'dan gelen ve PortBindings'de olmayan portları da ekle
                for port_proto in exposed_ports:
                    if port_proto not in ports:
                        ports[port_proto] = []
            
            container_info = {
                "id": container.id,
                "name": container.name,
                "status": container.status,
                "ports": ports,
                "image": container.image.tags[0] if container.image.tags else "-",
                "created": container.attrs["Created"],
                "state": container.attrs["State"],
                "compose_project": compose_project,
                "last_started": container.attrs["State"]["StartedAt"],
            }
            
            # Container'ın son başlatılma zamanını kaydet
            if container.status == 'running' and 'StartedAt' in container.attrs['State']:
                started_at = container.attrs["State"]["StartedAt"]
                try:
                    # ISO 8601 formatını Unix timestamp'e çevir
                    dt = datetime.datetime.fromisoformat(started_at.replace('Z', '+00:00'))
                    unix_timestamp = dt.timestamp()
                    self._container_restart_times[container_id] = unix_timestamp
                except Exception as e:
                    logger.error(f"StartedAt parsing hatası: {e}")
            
            return container_info
        except Exception as e:
            logger.error(f"Container bilgisi alınırken hata: {e}")
            return {"id": container_id, "error": str(e)}
            
    def _get_last_log_time(self, container_id):
        """Container için kaydedilmiş son log zamanını alır"""
        with self._last_log_times_lock:
            return self._container_last_log_times.get(container_id)
            
    def _update_last_log_time(self, container_id, timestamp):
        """Container için son log zamanını günceller"""
        with self._last_log_times_lock:
            # Timestamp bir ISO 8601 string ise, Unix timestamp'e dönüştür
            if isinstance(timestamp, str):
                try:
                    dt = datetime.datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    unix_timestamp = dt.timestamp()
                    self._container_last_log_times[container_id] = unix_timestamp
                except Exception as e:
                    logger.error(f"Timestamp dönüştürme hatası: {timestamp} - {e}")
            else:
                # Zaten sayısal bir timestamp ise direkt kaydet
                self._container_last_log_times[container_id] = timestamp

    def _is_log_duplicate(self, container_id, log_content):
        """Verilen log'un tekrar olup olmadığını kontrol eder"""
        with self._last_logs_lock:
            # Son N log'u tutan sözlük
            if container_id not in self._last_sent_logs:
                self._last_sent_logs[container_id] = []
                
            # Tüm log satırını kontrol et (zaman damgası dahil)
            # Aynı log zaman damgasına sahip ama farklı numaralarla gelmişse bile yakala
            
            # Önce tam eşleşme kontrolü yap
            if log_content in self._last_sent_logs[container_id]:
                return True
                
            # Zaman damgası kontrolü - aynı timestamp'e sahip logları kontrol et
            timestamp_part = log_content.split(" ", 1)[0] if " " in log_content else ""
            if timestamp_part:
                # Zaman damgası aynı olan tüm log satırlarını kontrol et
                for existing_log in self._last_sent_logs[container_id]:
                    existing_timestamp = existing_log.split(" ", 1)[0] if " " in existing_log else ""
                    if timestamp_part == existing_timestamp:
                        # Timestamp aynı, şimdi içerik kontrolü yap
                        log_content_part = log_content.split(" ", 1)[1] if " " in log_content else log_content
                        existing_content_part = existing_log.split(" ", 1)[1] if " " in existing_log else existing_log
                        
                        # İçerik yazılım tarafından eklenen benzersiz ID'ler olmadan karşılaştır
                        # Örneğin: "Ready to accept connections tcp" gibi mesajlar
                        if log_content_part == existing_content_part:
                            return True
                        
                        # Bazı durumlarda log formatında ufak farklar olabilir
                        # Son 30 karaktere odaklan - tipik olarak asıl log içeriğinin ana kısmını içerir
                        if len(log_content_part) > 30 and len(existing_content_part) > 30:
                            if log_content_part[-30:] == existing_content_part[-30:]:
                                return True
            
            # Ana içerik kısmına bak (timestamp sonrası)
            log_body = log_content.split(" ", 1)[1] if " " in log_content else log_content
            
            # Son N log içinde bu içerik var mı?
            for existing_log in self._last_sent_logs[container_id]:
                existing_body = existing_log.split(" ", 1)[1] if " " in existing_log else existing_log
                
                # Tam eşleşme
                if log_body == existing_body:
                    return True
                    
                # Redis'in son satırlarında sıkça görülen bir durum:
                # Aynı mesaj, farklı timestamp veya ufak variyasyonlar
                if "Ready to accept connections" in log_body and "Ready to accept connections" in existing_body:
                    return True
                    
                # Docker log'un iki kez gönderilme durumu için
                # Aynı mesajın basit varyasyonlarını yakala
                if len(log_body) > 15 and len(existing_body) > 15:
                    # İçerikteki son 15 karakter genellikle log'un özünü içerir
                    if log_body[-15:] == existing_body[-15:]:
                        return True
            
            # Log yeni - son gönderilenlere ekle ve en eski olanı çıkar
            self._last_sent_logs[container_id].append(log_content)
            
            # Son 15 log'u tut (değeri artırdım çünkü daha fazla geçmiş bilgisi tekrarları yakalamada yardımcı olur)
            if len(self._last_sent_logs[container_id]) > 15:  
                self._last_sent_logs[container_id].pop(0)
                
            return False
            
    def _clear_log_history(self, container_id):
        """Container için log geçmişini temizler"""
        with self._last_logs_lock:
            if container_id in self._last_sent_logs:
                self._last_sent_logs[container_id] = []
            
    def _get_container_restart_time(self, container_id):
        """Container'ın son restart zamanını döndürür"""
        # Container yeni restart olduysa, son restart zamanını al
        try:
            container = self.docker_client.containers.get(container_id)
            if container.status == 'running' and 'StartedAt' in container.attrs['State']:
                started_at = container.attrs["State"]["StartedAt"]
                try:
                    # ISO 8601 formatını Unix timestamp'e çevir
                    dt = datetime.datetime.fromisoformat(started_at.replace('Z', '+00:00'))
                    unix_timestamp = dt.timestamp()
                    self._container_restart_times[container_id] = unix_timestamp
                    return unix_timestamp
                except Exception as e:
                    logger.error(f"StartedAt parsing hatası: {e}")
        except Exception as e:
            logger.error(f"Container restart zamanı alınırken hata: {e}")
        
        # Kayıtlı restart zamanını döndür
        return self._container_restart_times.get(container_id, 0)
            
    def start_log_listener(self, container_id):
        """Belirli bir container için log dinleyicisi başlatır"""
        with self._log_listeners_lock:
            # Eğer bu container için zaten bir log listener çalışıyorsa, yeni başlatma
            if container_id in self._log_listeners and self._log_listeners[container_id].is_alive():
                logger.info(f"Container ID:{container_id} için log listener zaten çalışıyor")
                return
                
            try:
                # Container için durdurma sinyalini sıfırla
                self._stop_listening[container_id] = False
                
                # Log geçmişini temizle
                self._clear_log_history(container_id)
                
                # Yeni bir log dinleyici thread başlat
                log_thread = threading.Thread(
                    target=self._listen_container_logs, 
                    args=(container_id,),
                    daemon=True
                )
                self._log_listeners[container_id] = log_thread
                log_thread.start()
                logger.info(f"Container ID:{container_id} için log listener başlatıldı")
            except Exception as e:
                logger.error(f"Log listener başlatılırken hata: {e}")
    
    def stop_log_listener(self, container_id):
        """Belirli bir container için log dinleyicisini durdurur ve son logları da alır"""
        try:
            # Kapanış loglarını al
            container = None
            container_name = "unknown"
            
            try:
                # Container hala var mı kontrol et
                container = self.docker_client.containers.get(container_id)
                container_name = container.name
                
                # Son logları almak için timestamp'i kontrol et
                since_time = self._get_last_log_time(container_id)
                if since_time:
                    # Streaming olmadan son logları al
                    final_logs = container.logs(
                        stream=False,
                        timestamps=True,
                        since=since_time,
                        tail="all"  # Tüm son logları al
                    )
                    
                    # Logları işle ve gönder
                    if final_logs:
                        if isinstance(final_logs, bytes):
                            final_logs = final_logs.decode('utf-8', errors='replace')
                            
                        # Tekrarlama sorunu için işlenen logları sete kaydet
                        processed_logs = set()
                            
                        # Logları satırlara böl ve her satırı ayrı ayrı işle/gönder
                        log_lines = final_logs.strip().split('\n')
                        for log_line in log_lines:
                            if log_line.strip():  # Boş satırları atla
                                # Normalize edilmiş log satırını oluştur (timestamp sonrası kısım)
                                normalized_line = log_line.split(" ", 1)[-1] if " " in log_line else log_line
                                
                                # Bu log satırını daha önce işledik mi kontrol et
                                if normalized_line in processed_logs:
                                    continue
                                
                                # İşlenen logları kaydet
                                processed_logs.add(normalized_line)
                                
                                # Log verisini hazırla ve gönder
                                log_data = {
                                    "container_id": container_id,
                                    "container_name": container_name,
                                    "log": log_line
                                }
                                
                                # Tekrarlanan logları atla
                                if not self._is_log_duplicate(container_id, log_line):
                                    # Gönder
                                    self.send_event("container_log", log_data)
                                
                                # Timestamp varsa güncelle
                                parts = log_line.split(" ", 1)
                                if len(parts) >= 2 and parts[0]:
                                    self._update_last_log_time(container_id, parts[0])
            except docker.errors.NotFound:
                logger.warning(f"Container ID:{container_id} bulunamadı, son loglar alınamadı")
            except Exception as e:
                logger.error(f"Son loglar alınırken hata: {e}")
            
            # Thread'i durdur
            with self._log_listeners_lock:
                if container_id in self._log_listeners:
                    # Durdurma sinyali gönder
                    self._stop_listening[container_id] = True
                    logger.info(f"Container ID:{container_id} için log listener durduruluyor")
                    
                    # Thread listesinden kaldır
                    del self._log_listeners[container_id]
        except Exception as e:
            logger.error(f"Log listener durdurulurken hata: {e}")
                
    def _listen_container_logs(self, container_id):
        """
        Container loglarını dinler ve anında gönderir.
        Her container için ayrı bir thread olarak çalışır.
        Container yeniden başladığında sadece yeni logları gösterir.
        """
        try:
            container = self.docker_client.containers.get(container_id)
            container_name = container.name
            
            # Son log zamanını kontrol et (varsa)
            since_time = self._get_last_log_time(container_id)
            restart_time = self._get_container_restart_time(container_id)
            
            # Container yeni başlatıldıysa veya son log zamanı restart zamanından önce ise
            # (yani container restart olduysa), restart zamanını kullan
            if restart_time > 0 and (since_time is None or restart_time > since_time):
                logger.info(f"Container {container_name} (ID: {container_id}) yeniden başlatıldı, yeni loglar alınıyor")
                
                # Restart zamanından "çok hafif" öncesinden başla (0.5 saniye önceden)
                # Böylece container başlatıldığında üretilen ilk logları da yakalayabiliriz
                start_time = restart_time - 0.5
                
                # Log geçmişini temizle
                self._clear_log_history(container_id)
                
                # Log akışını başlat - hafifçe önceki zamandan itibaren
                log_stream = container.logs(
                    stream=True, 
                    follow=True, 
                    timestamps=True, 
                    since=start_time,
                    tail="all"  # tüm logları almak için
                )
                
                # Son zamanı güncelle
                self._update_last_log_time(container_id, restart_time)
                
            elif since_time:
                logger.info(f"Container {container_name} (ID: {container_id}) logları dinleniyor (son log zamanı: {since_time})")
                # Son log zamanından itibaren
                log_stream = container.logs(
                    stream=True, 
                    follow=True, 
                    timestamps=True, 
                    since=since_time
                )
            else:
                # İlk kez dinleniyorsa, tüm logları al
                logger.info(f"Container {container_name} (ID: {container_id}) logları dinleniyor (ilk dinleme)")
                
                # Şimdiki zamandan biraz öncesinden başla (0.5 saniye)
                unix_now = time.time() - 0.5
                
                # Log akışını başlat - şimdiki zamandan itibaren
                log_stream = container.logs(
                    stream=True, 
                    follow=True, 
                    timestamps=True, 
                    since=unix_now,
                    tail="all"  # tüm logları almak için
                )
                
                # Son zamanı güncelle
                self._update_last_log_time(container_id, unix_now)
            
            # Son log içeriğini takip etmek için
            last_log_content = None
            
            # Logları satır satır oku ve gönder
            for log_line in log_stream:
                # Log listener durduruldu mu kontrol et
                if self._stop_listening.get(container_id, False):
                    logger.info(f"Container {container_name} için log listener durduruldu (sinyal ile)")
                    break
                
                # Log listener thread'i durduruldu mu kontrol et
                with self._log_listeners_lock:
                    if container_id not in self._log_listeners:
                        logger.info(f"Container {container_name} için log listener durduruldu")
                        break
                
                try:
                    # Log satırını decode et
                    if isinstance(log_line, bytes):
                        log_line = log_line.decode('utf-8', errors='replace').strip()
                    
                    # Hızlı kontrol: Aynı log satırı hemen ardından geldiyse atla
                    if log_line == last_log_content:
                        continue
                    
                    # Son log içeriğini güncelle
                    last_log_content = log_line
                    
                    # Timestamp ve log içeriğini ayır (Docker logları "timestamp log_content" formatındadır)
                    parts = log_line.split(" ", 1)
                    if len(parts) >= 2:
                        timestamp, content = parts
                        # Son log zamanını güncelle (varsa)
                        if timestamp:
                            self._update_last_log_time(container_id, timestamp)
                    else:
                        # Timestamp bulunamadıysa, tüm satırı içerik olarak al
                        content = log_line
                    
                    # Gereksiz tekrarlayan zaman damgalarını temizle
                    if log_line.startswith(log_line[:26] + log_line[:26]):
                        log_line = log_line[26:]  # İlk zaman damgasını atla
                        
                    # Çift timestamp sorununu düzelt (Docker'ın bazı durumlarda ürettiği)
                    if log_line.startswith("T"):
                        # Bazı durumlarda Docker iki timestamp birleşik gelebiliyor
                        ts_parts = log_line.split(" ", 3)  # En fazla 3 parça 
                        if len(ts_parts) >= 3 and ts_parts[0].startswith("T") and ts_parts[1].startswith("T"):
                            # Timestamp düzeltmesi yap - ikinci timestamp'i al
                            log_line = ts_parts[1] + " " + " ".join(ts_parts[2:])
                    
                    # Tekrarlanan log satırını atla
                    if self._is_log_duplicate(container_id, log_line):
                        continue
                    
                    # Log verisini hazırla ve gönder
                    log_data = {
                        "container_id": container_id,
                        "container_name": container_name,
                        "log": log_line
                    }
                    
                    # Anında gönderim için queue'ya ekle
                    self.send_event("container_log", log_data)
                    
                except Exception as e:
                    logger.error(f"Log satırı işlenirken hata: {e}")
                
        except docker.errors.NotFound:
            logger.warning(f"Container ID:{container_id} bulunamadı, log dinleme sonlandırılıyor")
        except Exception as e:
            logger.error(f"Container ID:{container_id} logları dinlenirken hata: {e}")
            
            # Hata durumunda kısa bir süre bekleyip tekrar dene (eğer container hala çalışıyorsa)
            time.sleep(1)
            try:
                # Container hala var mı ve çalışıyor mu kontrol et
                container = self.docker_client.containers.get(container_id)
                if container.status == 'running' and not self._stop_listening.get(container_id, False):
                    logger.info(f"Container {container_name} için log listener yeniden başlatılıyor")
                    self.start_log_listener(container_id)
            except:
                pass
    
    def update_container_status(self):
        """
        Çalışan ve durmuş containerları kontrol eder, 
        gerekirse log dinleyicileri başlatır veya durdurur
        """
        try:
            # Çalışan tüm containerları al
            running_containers = set()
            for container in self.docker_client.containers.list(filters={"status": "running"}):
                running_containers.add(container.id)
                
                # Container restart olup olmadığını kontrol et ve son zamanı güncelle
                if container.status == 'running' and 'StartedAt' in container.attrs['State']:
                    started_at = container.attrs["State"]["StartedAt"]
                    try:
                        # ISO 8601 formatını Unix timestamp'e çevir
                        dt = datetime.datetime.fromisoformat(started_at.replace('Z', '+00:00'))
                        unix_timestamp = dt.timestamp()
                        self._container_restart_times[container.id] = unix_timestamp
                    except Exception:
                        pass
                
            # Önceki durumla karşılaştır ve güncelle
            with self._containers_lock:
                # Yeni başlayan containerlar
                new_running = running_containers - self._running_containers
                for container_id in new_running:
                    logger.info(f"Yeni çalışan container: {container_id}")
                    self.start_log_listener(container_id)
                
                # Duran containerlar
                stopped_running = self._running_containers - running_containers
                for container_id in stopped_running:
                    logger.info(f"Duran container: {container_id}")
                    self.stop_log_listener(container_id)
                
                # Çalışan container listesini güncelle
                self._running_containers = running_containers
        
        except Exception as e:
            logger.error(f"Container durumları güncellenirken hata: {e}")

    async def send_periodic_docker_info(self):
        """Her 5 saniyede bir tüm container bilgilerini gönderir"""
        self._periodic_info_running = True
        logger.info("Periyodik container bilgi gönderimi başlatıldı (5 saniye aralıklarla)")
        
        while True:
            try:
                # Container durumlarını kontrol et ve log dinleyicileri güncelle
                self.update_container_status()
                
                # Tüm container bilgilerini gönder
                container_info = self.get_docker_info()
                self.send_event("docker_info", container_info)
                logger.info(f"Periyodik container bilgileri gönderildi ({len(container_info)} container)")
            except Exception as e:
                logger.error(f"Periyodik Docker bilgisi gönderirken hata: {e}")
                sentry_sdk.capture_exception(e)
            
            # Tam olarak 5 saniye bekle - sleep'in kendisi başka işlemleri engellemez
            await asyncio.sleep(5)

    def docker_event_listener(self):
        """Docker eventlerini dinler ve anında gönderir"""
        while True:
            try:
                for event in self.docker_client.events(decode=True):
                    if event["Type"] == "container":
                        action = event.get('Action', '')
                        logger.info(f"Docker container event alındı: {action}")
                        
                        # Doğrudan ham event'i gönder
                        self.send_event("docker_event", event)
                        
                        # Container ID'yi al ve ilgili container'ın bilgisini çek
                        container_id = event.get("id", event.get("Actor", {}).get("ID", None))
                        if container_id:
                            # Sadece ilgili container'ın bilgisini getir
                            container_info = self.get_single_container_info(container_id)
                            
                            # Container bilgisini ayrı bir event olarak gönder
                            self.send_event("docker_event_one", container_info)
                            logger.info(f"Container ID:{container_id} için bilgiler gönderildi")
                            
                            # Container başladı veya durdu mu kontrol et
                            if action in ['start', 'restart', 'unpause']:
                                # Container restart olduysa, log dinleyicisini başlat
                                self.start_log_listener(container_id)
                            elif action in ['die', 'stop', 'kill', 'pause']:
                                self.stop_log_listener(container_id)
                        else:
                            logger.warning("Event içinde container ID bulunamadı")
            except Exception as e:
                logger.error(f"Docker event listener hatası: {e}")
                time.sleep(5)  # Hata durumunda kısa bir bekleme

    async def start(self):
        """Tüm servisleri başlatır"""
        asyncio.create_task(self.connect_to_websocket())
        
        thread = threading.Thread(target=self.docker_event_listener)
        thread.daemon = True
        thread.start()
        
        self.update_container_status()
        
        await asyncio.sleep(1)

        asyncio.create_task(self.send_periodic_docker_info())
        
        logger.info("DockerSystemManager başlatıldı")

async def main():
    docker_system_manager = DockerSystemManager()
    await docker_system_manager.start()
    
    try:
        # Sonsuza kadar çalış
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Uygulama kullanıcı tarafından sonlandırıldı")
    except Exception as e:
        logger.error(f"Beklenmeyen hata: {e}")
        sentry_sdk.capture_exception(e)
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())



