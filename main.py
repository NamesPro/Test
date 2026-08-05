import os
import sys
import time
import threading
import flet as ft

# Настройки путей и логики
TXT_PATH = "/storage/emulated/0/Download/skin_ids.txt"
LOAD_THRESHOLD = 300000
STABILIZE_TIME = 3
REVERT_DELAY = 30

def load_skins_from_txt():
    """Парсит skin_ids.txt и возвращает словарь { 'Название': ID }"""
    skins = {}
    if not os.path.exists(TXT_PATH):
        return skins
    
    try:
        with open(TXT_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "public const" in line and "=" in line:
                    try:
                        parts = line.split("=")
                        name_part = parts[0].strip().split()[-1]
                        id_part = parts[1].replace(";", "").strip()
                        skins[name_part] = int(id_part)
                    except:
                        pass
    except Exception as e:
        print(f"[-] Ошибка чтения файла: {e}")
    return skins

def find_pid(process_name):
    for pid in os.listdir('/proc'):
        if pid.isdigit():
            try:
                with open(f'/proc/{pid}/cmdline', 'rb') as f:
                    if process_name in f.read().decode('utf-8', errors='ignore'):
                        return int(pid)
            except:
                pass
    return None

def get_writable_regions(pid):
    regions = []
    try:
        with open(f"/proc/{pid}/maps", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and "rw-p" in parts[1]:
                    addr_range = parts[0].split("-")
                    regions.append((int(addr_range[0], 16), int(addr_range[1], 16)))
    except:
        pass
    return regions

def find_and_replace(pid, old_bytes, new_bytes):
    regions = get_writable_regions(pid)
    if not regions:
        return [], False
    
    replaced = []
    try:
        mem_file = open(f"/proc/{pid}/mem", "r+b", buffering=0)
    except:
        return [], False
    
    for start, end in regions:
        size = end - start
        if size <= 0 or size > 50 * 1024 * 1024:
            continue
        try:
            mem_file.seek(start)
            buffer = mem_file.read(min(size, 10 * 1024 * 1024))
            pos = 0
            while True:
                pos = buffer.find(old_bytes, pos)
                if pos == -1:
                    break
                target = start + pos
                mem_file.seek(target)
                mem_file.write(new_bytes)
                replaced.append(target)
                pos += len(old_bytes)
        except:
            pass
    mem_file.close()
    return replaced, len(replaced) > 0

def revert_changes(pid, addresses, old_bytes, new_bytes):
    if not addresses:
        return False
    try:
        mem_file = open(f"/proc/{pid}/mem", "r+b", buffering=0)
    except:
        return False
    
    reverted = 0
    for addr in addresses:
        try:
            mem_file.seek(addr)
            if mem_file.read(len(new_bytes)) == new_bytes:
                mem_file.seek(addr)
                mem_file.write(old_bytes)
                reverted += 1
        except:
            pass
    mem_file.close()
    return reverted > 0

def get_memory_usage(pid):
    try:
        with open(f"/proc/{pid}/status", "r") as f:
            for line in f:
                if "VmRSS" in line:
                    return int(line.split()[1])
    except:
        return 0

def check_root():
    """Проверка наличия root-прав"""
    try:
        return os.getuid() == 0
    except:
        return False

class SkinManagerApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.page.title = "Standoff 2 Skin Manager"
        self.page.window_width = 400
        self.page.window_height = 600
        self.page.theme_mode = ft.ThemeMode.DARK
        
        self.skins_dict = load_skins_from_txt()
        self.skin_names = list(self.skins_dict.keys())
        
        # Глобальные переменные состояния
        self.config = {
            "mode": "auto",
            "old_id": None,
            "new_id": None,
            "is_running": False,
            "last_addresses": [],
            "worker_thread": None
        }
        
        # Создаем элементы управления
        self.status_text = ft.Text("Статус: Остановлен", color=ft.Colors.RED, weight=ft.FontWeight.BOLD)
        self.log_output = ft.Text("Логи: Ожидание действий...", size=12, color=ft.Colors.GREY_400)
        
        self.setup_ui()
        
        # Проверка root
        if not check_root():
            self.log_output.value = "⚠️ Требуются root-права для работы с памятью!"
            self.page.update()
    
    def setup_ui(self):
        """Настройка интерфейса"""
        # ВКЛАДКА: IN
        self.setup_inv_tab()
        
        # ВКЛАДКА: SETTING
        self.setup_setting_tab()
        
        # ВКЛАДКА: START
        self.setup_start_tab()
        
        # ИСПРАВЛЕНО: Используем Tab с параметром tab_content
        tabs = ft.Tabs(
            selected_index=0,
            animation_duration=300,
            tabs=[
                ft.Tab(
                    text="Start",
                    tab_content=ft.Container(content=self.start_tab, padding=10)
                ),
                ft.Tab(
                    text="Inv",
                    tab_content=ft.Container(content=self.inv_tab, padding=10)
                ),
                ft.Tab(
                    text="Setting",
                    tab_content=ft.Container(content=self.setting_tab, padding=10)
                ),
            ],
            expand=1
        )
        self.page.add(tabs)
    
    def setup_inv_tab(self):
        """Настройка вкладки инвентаря"""
        self.old_search = ft.TextField(label="Найти старый скин (например, M4A1)", text_size=12)
        self.old_suggestions = ft.ListView(expand=1, spacing=2, padding=5, auto_scroll=False, height=100)
        
        self.new_search = ft.TextField(label="Найти новый скин (например, JadeStone)", text_size=12)
        self.new_suggestions = ft.ListView(expand=1, spacing=2, padding=5, auto_scroll=False, height=100)
        
        self.selected_old_txt = ft.Text("Старый: Не выбран", size=12, color=ft.Colors.YELLOW)
        self.selected_new_txt = ft.Text("Новый: Не выбран", size=12, color=ft.Colors.GREEN)
        
        self.old_search.on_change = lambda e: self.filter_skins(e, True)
        self.new_search.on_change = lambda e: self.filter_skins(e, False)
        
        self.inv_tab = ft.Column(
            [
                ft.Text(f"Загружено скинов из TXT: {len(self.skins_dict)}", size=12, color=ft.Colors.BLUE_200),
                self.old_search,
                self.old_suggestions,
                self.selected_old_txt,
                ft.Divider(height=1),
                self.new_search,
                self.new_suggestions,
                self.selected_new_txt,
            ],
            scroll=ft.ScrollMode.AUTO,
            spacing=10
        )
    
    def filter_skins(self, e, is_old=True):
        query = e.control.value.lower()
        s_list = self.old_suggestions if is_old else self.new_suggestions
        s_list.controls.clear()
        
        if len(query) > 1:
            matches = [name for name in self.skin_names if query in name.lower()][:15]
            for name in matches:
                s_list.controls.append(
                    ft.ListTile(
                        title=ft.Text(f"{name} ({self.skins_dict[name]})", size=12),
                        on_click=lambda _, n=name, io=is_old: self.select_skin(n, io)
                    )
                )
        self.page.update()
    
    def select_skin(self, name, is_old):
        skin_id = self.skins_dict[name]
        if is_old:
            self.config["old_id"] = skin_id
            self.old_search.value = name
            self.old_suggestions.controls.clear()
            self.selected_old_txt.value = f"Старый: {name} [{skin_id}]"
        else:
            self.config["new_id"] = skin_id
            self.new_search.value = name
            self.new_suggestions.controls.clear()
            self.selected_new_txt.value = f"Новый: {name} [{skin_id}]"
        self.page.update()
    
    def setup_setting_tab(self):
        """Настройка вкладки настроек"""
        self.mode_dropdown = ft.Dropdown(
            label="Режим работы",
            value="auto",
            options=[
                ft.dropdown.Option("auto", "Автоматический"),
                ft.dropdown.Option("manual", "Ручной"),
            ],
            width=250
        )
        self.mode_dropdown.on_change = self.on_mode_change
        
        self.setting_tab = ft.Column(
            [
                ft.Text("Параметры запуска", weight=ft.FontWeight.BOLD),
                self.mode_dropdown,
                ft.Text(
                    "В автоматическом режиме скрипт сам ждет загрузку матча.\nВ ручном вы управляете кнопками на вкладке Start.",
                    size=11,
                    color=ft.Colors.GREY_500
                ),
            ],
            spacing=15
        )
    
    def setup_start_tab(self):
        """Настройка вкладки запуска"""
        self.manual_controls = ft.Column(
            [
                ft.ElevatedButton(
                    "Начать замену",
                    on_click=self.manual_apply,
                    bgcolor=ft.Colors.BLUE,
                    color=ft.Colors.WHITE,
                    width=200
                ),
                ft.ElevatedButton(
                    "Бекнуть замену",
                    on_click=self.manual_revert,
                    bgcolor=ft.Colors.ORANGE,
                    color=ft.Colors.WHITE,
                    width=200
                ),
            ],
            spacing=10,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            visible=False
        )
        
        self.start_stop_btn = ft.ElevatedButton(
            "Запустить авто",
            on_click=self.toggle_auto_start,
            bgcolor=ft.Colors.GREEN,
            color=ft.Colors.WHITE,
            width=200
        )
        
        self.start_tab = ft.Column(
            [
                self.status_text,
                ft.Divider(height=1),
                self.start_stop_btn,
                self.manual_controls,
                ft.Divider(height=1),
                ft.Text("Логи выполнения:", weight=ft.FontWeight.BOLD),
                self.log_output
            ],
            alignment=ft.MainAxisAlignment.START,
            spacing=10
        )
    
    def on_mode_change(self, e):
        if self.mode_dropdown.value == "manual":
            self.config["is_running"] = False
            self.manual_controls.visible = True
            self.start_stop_btn.visible = False
            self.status_text.value = "Статус: Ручной режим"
            self.status_text.color = ft.Colors.ORANGE
        else:
            self.manual_controls.visible = False
            self.start_stop_btn.visible = True
            self.status_text.value = "Статус: Остановлен"
            self.status_text.color = ft.Colors.RED
            self.start_stop_btn.text = "Запустить авто"
            self.start_stop_btn.bgcolor = ft.Colors.GREEN
        self.page.update()
    
    def log(self, message):
        """Добавление сообщения в логи"""
        self.log_output.value = message
        self.page.update()
    
    def toggle_auto_start(self, e):
        if not self.config["old_id"] or not self.config["new_id"]:
            self.log("[-] Выберите скины во вкладке INV!")
            return
        
        if not check_root():
            self.log("[-] Требуются root-права!")
            return
        
        if not self.config["is_running"]:
            self.config["is_running"] = True
            self.status_text.value = "Статус: Работает (Авто-мониторинг)"
            self.status_text.color = ft.Colors.GREEN
            self.start_stop_btn.text = "Остановить авто"
            self.start_stop_btn.bgcolor = ft.Colors.RED
            
            self.config["worker_thread"] = threading.Thread(target=self.worker_loop, daemon=True)
            self.config["worker_thread"].start()
        else:
            self.config["is_running"] = False
            self.status_text.value = "Статус: Остановлен"
            self.status_text.color = ft.Colors.RED
            self.start_stop_btn.text = "Запустить авто"
            self.start_stop_btn.bgcolor = ft.Colors.GREEN
        self.page.update()
    
    def worker_loop(self):
        """Основной рабочий цикл"""
        pid = find_pid("com.axlebolt.standoff2")
        if not pid:
            self.log("[-] Игра не запущена!")
            self.config["is_running"] = False
            self.page.update()
            return

        old_bytes = self.config["old_id"].to_bytes(4, byteorder="little")
        new_bytes = self.config["new_id"].to_bytes(4, byteorder="little")

        initial_mem = get_memory_usage(pid)
        loading_detected = False
        peak_mem = 0
        stable_count = 0
        last_mem = 0

        while self.config["is_running"]:
            # Проверка, что игра все еще запущена
            if not find_pid("com.axlebolt.standoff2"):
                self.log("[-] Игра была закрыта!")
                self.config["is_running"] = False
                self.page.update()
                break
            
            current_mem = get_memory_usage(pid)
            if current_mem > 0:
                diff = current_mem - initial_mem
                
                if diff > LOAD_THRESHOLD and not loading_detected:
                    loading_detected = True
                    peak_mem = current_mem
                    self.log("[+] Начало загрузки матча...")

                if loading_detected:
                    if current_mem > peak_mem:
                        peak_mem = current_mem
                    
                    if last_mem > 0:
                        change = abs(current_mem - last_mem)
                        if change < 5000:
                            stable_count += 1
                            if stable_count >= STABILIZE_TIME:
                                self.log("[+] 100% загрузки! Применяем скин...")
                                
                                addrs, success = find_and_replace(pid, old_bytes, new_bytes)
                                if success:
                                    self.config["last_addresses"] = addrs
                                    self.log(f"[+] Успешно заменено: {len(addrs)} адресов")
                                    
                                    # Обратный отсчет
                                    for i in range(REVERT_DELAY, 0, -1):
                                        if not self.config["is_running"]:
                                            break
                                        self.log(f"[*] Авто-откат через {i} сек...")
                                        time.sleep(1)
                                    
                                    if self.config["is_running"]:
                                        revert_changes(pid, addrs, old_bytes, new_bytes)
                                        self.log("[+] Изменения откачены.")
                                else:
                                    self.log("[-] Не удалось найти адреса для замены.")
                                
                                loading_detected = False
                                stable_count = 0
                        else:
                            if change > 10000:
                                stable_count = 0
                    last_mem = current_mem
            time.sleep(1)
        
        # Очистка состояния при выходе
        self.config["is_running"] = False
        self.page.update()
    
    def manual_apply(self, e):
        if not self.config["old_id"] or not self.config["new_id"]:
            self.log("[-] Сначала выберите скины во вкладке INV!")
            return
        
        if not check_root():
            self.log("[-] Требуются root-права!")
            return
        
        pid = find_pid("com.axlebolt.standoff2")
        if not pid:
            self.log("[-] Игра не запущена!")
            return
        
        old_bytes = self.config["old_id"].to_bytes(4, byteorder="little")
        new_bytes = self.config["new_id"].to_bytes(4, byteorder="little")
        
        addrs, success = find_and_replace(pid, old_bytes, new_bytes)
        if success:
            self.config["last_addresses"] = addrs
            self.log(f"[+] Ручная замена успешна! Найдено: {len(addrs)} адресов")
        else:
            self.log("[-] Не удалось найти адреса для замены.")
    
    def manual_revert(self, e):
        if not self.config["last_addresses"]:
            self.log("[-] Нечего откатывать!")
            return
        
        if not check_root():
            self.log("[-] Требуются root-права!")
            return
        
        pid = find_pid("com.axlebolt.standoff2")
        if not pid:
            self.log("[-] Игра не запущена!")
            return
        
        old_bytes = self.config["old_id"].to_bytes(4, byteorder="little")
        new_bytes = self.config["new_id"].to_bytes(4, byteorder="little")
        
        if revert_changes(pid, self.config["last_addresses"], old_bytes, new_bytes):
            self.log("[+] Успешный ручной откат!")
            self.config["last_addresses"] = []
        else:
            self.log("[-] Ошибка отката.")

def main(page: ft.Page):
    app = SkinManagerApp(page)

if __name__ == "__main__":
    ft.app(target=main)
