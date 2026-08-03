import os
import sys
import json
import re
import time
import threading
import ctypes
from ctypes import wintypes
import tkinter as tk
from tkinter import ttk, messagebox

# Win32 API Definitions for Window Docking
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long)
    ]

def clean_html(text):
    if not text:
        return ""
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</p>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'[\r\n]+', '\n', text)
    return text.strip()

class Win32DockEngine:
    """Windows API 窗口吸附引擎"""
    @staticmethod
    def get_process_name(hwnd):
        try:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if not pid.value:
                return ""
            h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
            if not h_proc:
                return ""
            buff = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            res = kernel32.QueryFullProcessImageNameW(h_proc, 0, buff, ctypes.byref(size))
            kernel32.CloseHandle(h_proc)
            if res:
                return os.path.basename(buff.value)
        except Exception:
            pass
        return ""

    @classmethod
    def find_ets_hwnd(cls):
        target = [None]
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def enum_cb(hwnd, lparam):
            if not user32.IsWindowVisible(hwnd):
                return True

            # 1. Match process name strictly (ETSShell.exe)
            pname = cls.get_process_name(hwnd)
            if pname and pname.lower() == 'etsshell.exe':
                target[0] = hwnd
                return False

            # 2. Match window title strictly containing "E听说" or "ETSShell" (strictly exclude generic "ETS" and "ETSHelper")
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buff, length + 1)
                title = buff.value
                if ("E听说" in title or "ETSShell" in title) and "ETSHelper" not in title and "试题助手" not in title:
                    target[0] = hwnd
                    return False
            return True

        user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
        return target[0]

    @staticmethod
    def get_window_rect(hwnd):
        if not hwnd or not user32.IsWindow(hwnd):
            return None
        rect = RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
        return None

    @staticmethod
    def is_minimized(hwnd):
        if not hwnd or not user32.IsWindow(hwnd):
            return False
        return bool(user32.IsIconic(hwnd))

    @staticmethod
    def is_zoomed(hwnd):
        if not hwnd or not user32.IsWindow(hwnd):
            return False
        return bool(user32.IsZoomed(hwnd))

    @staticmethod
    def get_work_area():
        rect = RECT()
        user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
        return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top

class SectionData:
    """解耦的数据模型"""
    def __init__(self, section_idx, folder_name, passage=""):
        self.section_idx = section_idx
        self.folder_name = folder_name
        self.passage = passage
        self.choices = {}     # num -> {'title': str, 'options': list, 'answer': str}
        self.fills = {}       # num -> answer_str
        self.dialogue = []    # list of {'num': str, 'ask': str, 'std_answers': list, 'keywords': str}

    @property
    def section_type(self):
        if self.fills:
            return "fill"
        elif self.choices:
            return "choice"
        elif self.dialogue:
            if self.dialogue and not self.dialogue[0].get('ask'):
                return "retell"
            return "dialogue"
        elif self.passage:
            return "reading"
        return "empty"

class ETSExtractor:
    @staticmethod
    def get_ets_dir():
        appdata = os.getenv('APPDATA')
        if appdata:
            return os.path.join(appdata, 'ETS')
        return ""

    @staticmethod
    def parse_content_folder(content_path, section_idx):
        info_file = os.path.join(content_path, 'info.json')
        content2_file = os.path.join(content_path, 'content2.json')
        content_file = os.path.join(content_path, 'content.json')

        sec = SectionData(section_idx, os.path.basename(content_path))

        info_data = None
        if os.path.exists(info_file):
            try:
                with open(info_file, 'r', encoding='utf-8', errors='ignore') as f:
                    info_data = json.load(f)
            except Exception:
                pass

        c_data = None
        target_c_file = content2_file if os.path.exists(content2_file) else content_file
        if os.path.exists(target_c_file):
            try:
                with open(target_c_file, 'r', encoding='utf-8', errors='ignore') as f:
                    c_data = json.load(f)
            except Exception:
                pass

        # 1. Parse info.json
        if info_data and isinstance(info_data, list):
            for item in info_data:
                code_id = item.get('code_id', '')
                code_class = item.get('code_class', '')

                if code_class == 'choose_content' or 'code_json_obj' in item:
                    try:
                        obj = json.loads(item.get('code_json_obj', '{}'))
                        sec.passage = clean_html(obj.get('st_nr', ''))
                        for xt in obj.get('xtlist', []):
                            num = xt.get('xt_xh', '')
                            title = clean_html(xt.get('xt_nr', ''))
                            ans = xt.get('answer', '')
                            options = [f"{opt.get('xx_mc')}. {clean_html(opt.get('xx_nr'))}" for opt in xt.get('xxlist', [])]
                            sec.choices[num] = {
                                'title': title,
                                'options': options,
                                'answer': ans
                            }
                    except Exception:
                        pass

                elif code_class == 'choose_answer' or 'code_json_array' in item:
                    try:
                        arr = json.loads(item.get('code_json_array', '[]'))
                        for a in arr:
                            num = a.get('xth', '')
                            if num and a.get('answer'):
                                if num in sec.choices:
                                    sec.choices[num]['answer'] = a.get('answer')
                    except Exception:
                        pass

                elif code_class == 'fill_answer':
                    try:
                        arr = json.loads(item.get('code_json_array', '[]'))
                        for a in arr:
                            num = a.get('xth', '')
                            ans = a.get('answer', '')
                            if num and ans:
                                sec.fills[num] = ans
                    except Exception:
                        pass

                elif code_id in ['value', 'value_all'] and not sec.passage:
                    sec.passage = clean_html(item.get('code_value', ''))

        # 2. Parse c_data (content2.json / content.json)
        if isinstance(c_data, dict):
            struct_type = c_data.get('structure_type', '')
            info_dict = c_data.get('info', {})

            if not sec.passage and isinstance(info_dict, dict) and 'st_nr' in info_dict:
                sec.passage = clean_html(info_dict.get('st_nr', ''))
            elif not sec.passage and isinstance(info_dict, dict) and 'value' in info_dict:
                sec.passage = clean_html(info_dict.get('value', ''))

            if isinstance(info_dict, dict) and 'xtlist' in info_dict and not sec.choices:
                for xt in info_dict.get('xtlist', []):
                    num = xt.get('xt_xh', '')
                    title = clean_html(xt.get('xt_nr', ''))
                    ans = xt.get('answer', '')
                    options = [f"{opt.get('xx_mc')}. {clean_html(opt.get('xx_nr'))}" for opt in xt.get('xxlist', [])]
                    sec.choices[num] = {
                        'title': title,
                        'options': options,
                        'answer': ans
                    }

            # Check for Fill in the Blanks in c_data
            if (struct_type == 'collector.fill' or not sec.fills) and isinstance(info_dict, dict) and 'std' in info_dict:
                std_items = info_dict.get('std', [])
                if isinstance(std_items, list) and std_items and isinstance(std_items[0], dict) and 'xth' in std_items[0]:
                    for s in std_items:
                        num = s.get('xth', '')
                        ans = clean_html(s.get('value', ''))
                        if num and ans:
                            sec.fills[num] = ans

            # Flexible Q&A parsing (ONLY if NOT choice and NOT fill)
            if not sec.fills and not sec.choices:
                q_list = []
                if isinstance(info_dict, list):
                    q_list = info_dict
                elif isinstance(info_dict, dict):
                    if 'question' in info_dict:
                        q_list = info_dict['question']
                    elif 'question_list' in info_dict:
                        q_list = info_dict['question_list']
                    elif 'questions' in info_dict:
                        q_list = info_dict['questions']
                    elif 'ask' in info_dict or ('std' in info_dict and not sec.fills):
                        q_list = [info_dict]
                elif isinstance(c_data, list):
                    q_list = c_data

                for q in q_list:
                    if isinstance(q, dict) and ('ask' in q or 'std' in q):
                        ask_num = q.get('xh', '')
                        ask_text = clean_html(q.get('ask', ''))
                        std_ans_list = [clean_html(s.get('value', '')) for s in q.get('std', []) if isinstance(s, dict) and s.get('value')]
                        unique_std = []
                        for s in std_ans_list:
                            if s and s not in unique_std:
                                unique_std.append(s)
                        keywords = q.get('keywords', '')
                        sec.dialogue.append({
                            'num': ask_num,
                            'ask': ask_text,
                            'std_answers': unique_std,
                            'keywords': keywords
                        })

        return sec

    @classmethod
    def parse_exam_set(cls, folder_path):
        content_dirs = [d for d in os.listdir(folder_path) if d.startswith('content_') and os.path.isdir(os.path.join(folder_path, d))]
        content_dirs.sort(key=lambda x: int(x.split('_')[1]) if x.split('_')[1].isdigit() else x)

        sections = []
        for idx, c_dir in enumerate(content_dirs, 1):
            c_path = os.path.join(folder_path, c_dir)
            sec = cls.parse_content_folder(c_path, idx)
            sections.append(sec)

        return sections

class ETSHelperApp:
    def __init__(self, root):
        self.root = root
        self.root.title("E听说试题助手 (ETSHelper)")
        self.root.geometry("1000x650")
        self.root.minsize(260, 250)

        self.ets_dir = ETSExtractor.get_ets_dir()
        self.known_sets = {}
        self.current_folder_id = None
        self.current_sections = []
        self.is_mini_mode = False

        self.last_target_rect = None

        # Frameless window dragging coordinates
        self._drag_x = 0
        self._drag_y = 0

        self.setup_ui()
        self.start_monitoring()
        self.start_docking_loop()

    def setup_ui(self):
        style = ttk.Style()
        style.theme_use('clam')

        self.top_frame = ttk.Frame(self.root, padding=4)
        self.top_frame.pack(fill=tk.X)

        self.top_frame.bind("<ButtonPress-1>", self._start_drag)
        self.top_frame.bind("<B1-Motion>", self._do_drag)

        self.status_label = ttk.Label(self.top_frame, text="已载入 0 套试题", font=("Microsoft YaHei", 8, "bold"))
        self.status_label.pack(side=tk.LEFT, padx=3)
        self.status_label.bind("<ButtonPress-1>", self._start_drag)
        self.status_label.bind("<B1-Motion>", self._do_drag)

        self.path_label = ttk.Label(self.top_frame, text="", font=("Microsoft YaHei", 8), foreground="gray")
        self.path_label.pack(side=tk.LEFT, padx=5)

        self.btn_mode = ttk.Button(self.top_frame, text="极简悬浮", command=self.toggle_mode)
        self.btn_mode.pack(side=tk.RIGHT, padx=2)

        self.btn_refresh = ttk.Button(self.top_frame, text="刷新", command=self.refresh_exam_list)
        self.btn_refresh.pack(side=tk.RIGHT, padx=2)

        self.paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        self.paned.pack(fill=tk.BOTH, expand=True, padx=3, pady=(0, 3))

        self.left_frame = ttk.LabelFrame(self.paned, text="试题集列表", padding=3)
        self.paned.add(self.left_frame, weight=1)

        self.tree = ttk.Treeview(self.left_frame, columns=("id", "time"), show="headings", selectmode="browse")
        self.tree.heading("id", text="试题编号")
        self.tree.heading("time", text="更新时间")
        self.tree.column("id", width=90, anchor=tk.CENTER)
        self.tree.column("time", width=120, anchor=tk.CENTER)

        tree_scroll = ttk.Scrollbar(self.left_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind("<<TreeviewSelect>>", self.on_select_exam_set)

        self.right_frame = ttk.LabelFrame(self.paned, text="答案与题目预览", padding=3)
        self.paned.add(self.right_frame, weight=3)

        self.text_display = tk.Text(self.right_frame, wrap=tk.WORD, font=("Microsoft YaHei", 9), bg="#F8F9FA", relief=tk.FLAT)
        text_scroll = ttk.Scrollbar(self.right_frame, orient=tk.VERTICAL, command=self.text_display.yview)
        self.text_display.configure(yscrollcommand=text_scroll.set)

        self.text_display.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        text_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self._configure_text_tags()

    def _start_drag(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _do_drag(self, event):
        deltax = event.x - self._drag_x
        deltay = event.y - self._drag_y
        x = self.root.winfo_x() + deltax
        y = self.root.winfo_y() + deltay
        self.root.geometry(f"+{x}+{y}")

    def _configure_text_tags(self):
        # Normal Mode Tags
        self.text_display.tag_config("h1", font=("Microsoft YaHei", 10, "bold"), foreground="#0D6EFD")
        self.text_display.tag_config("h2", font=("Microsoft YaHei", 9, "bold"), foreground="#198754")
        self.text_display.tag_config("answer", font=("Microsoft YaHei", 9, "bold"), foreground="#DC3545")
        self.text_display.tag_config("keyword", font=("Microsoft YaHei", 8, "bold"), foreground="#6f42c1")
        self.text_display.tag_config("sub", font=("Microsoft YaHei", 8), foreground="#6C757D")

        # Mini Mode Tags (Crisp Dracula Theme)
        self.text_display.tag_config("mini_header", font=("Microsoft YaHei", 9, "bold"), foreground="#8BE9FD")
        self.text_display.tag_config("mini_sec_title", font=("Microsoft YaHei", 9, "bold"), foreground="#FF79C6")
        self.text_display.tag_config("tag_choice", font=("Microsoft YaHei", 9, "bold"), foreground="#50FA7B")
        self.text_display.tag_config("tag_fill", font=("Microsoft YaHei", 9, "bold"), foreground="#8BE9FD")
        self.text_display.tag_config("tag_qa", font=("Microsoft YaHei", 9, "bold"), foreground="#FFB86C")
        self.text_display.tag_config("tag_retell", font=("Microsoft YaHei", 9, "bold"), foreground="#BD93F9")
        self.text_display.tag_config("tag_read", font=("Microsoft YaHei", 9, "bold"), foreground="#6272A4")
        self.text_display.tag_config("tag_label", font=("Microsoft YaHei", 8, "bold"), foreground="#FF79C6")
        self.text_display.tag_config("tag_ans_text", font=("Microsoft YaHei", 9, "bold"), foreground="#50FA7B")
        self.text_display.tag_config("tag_sub", font=("Microsoft YaHei", 8), foreground="#6272A4")

    def toggle_mode(self):
        self.is_mini_mode = not self.is_mini_mode

        if self.is_mini_mode:
            if str(self.left_frame) in self.paned.panes():
                self.paned.forget(self.left_frame)
            
            # Reset window state to normal if ETSHelper was maximized in Full Mode
            if self.root.state() == 'zoomed':
                self.root.state('normal')

            self.root.overrideredirect(True)
            self.root.wm_attributes("-topmost", True)
            self.root.geometry("280x420")
            self.text_display.config(bg="#191A21", fg="#F8F8F2")
            self.btn_mode.config(text="完整大模式")
            self.path_label.pack_forget()
            self.sync_dock_position(force=True)
        else:
            if str(self.left_frame) not in self.paned.panes():
                self.paned.insert(0, self.left_frame, weight=1)
            self.root.overrideredirect(False)
            self.root.wm_attributes("-topmost", False)
            if self.root.state() == 'withdrawn':
                self.root.deiconify()
            self.root.geometry("1000x650")
            self.text_display.config(bg="#F8F9FA", fg="#000000")
            self.btn_mode.config(text="极简悬浮")
            self.path_label.pack(side=tk.LEFT, padx=5)

        self.render_current_display()

    def start_docking_loop(self):
        def loop():
            if self.is_mini_mode:
                self.sync_dock_position()
            self.root.after(40, loop)
        self.root.after(100, loop)

    def sync_dock_position(self, force=False):
        if not self.is_mini_mode:
            return

        hwnd = Win32DockEngine.find_ets_hwnd()
        if not hwnd:
            if self.root.state() == 'withdrawn':
                self.root.deiconify()
            return

        if Win32DockEngine.is_minimized(hwnd):
            if self.root.state() != 'withdrawn':
                self.root.withdraw()
            return
        else:
            if self.root.state() == 'withdrawn':
                self.root.deiconify()

        rect_info = Win32DockEngine.get_window_rect(hwnd)
        if not rect_info:
            return

        left, top, width, height = rect_info
        is_max = Win32DockEngine.is_zoomed(hwnd)
        wa_left, wa_top, wa_w, wa_h = Win32DockEngine.get_work_area()

        cache_key = (rect_info, is_max)
        if not force and self.last_target_rect == cache_key:
            return
        self.last_target_rect = cache_key

        dock_width = 280
        dock_h = min(height if not is_max else 520, wa_h - 20)

        if is_max:
            # ETSShell is maximized: pin floating HUD to top-right corner of screen work area
            dock_x = wa_left + wa_w - dock_width - 10
            dock_y = wa_top + 10
        else:
            # ETSShell is windowed: dock to right edge
            dock_x = left + width + 2
            dock_y = top
            if dock_x + dock_width > wa_left + wa_w:
                dock_x = left - dock_width - 2
                if dock_x < wa_left:
                    dock_x = wa_left + wa_w - dock_width - 10

        self.root.geometry(f"{dock_width}x{dock_h}+{dock_x}+{dock_y}")

    def start_monitoring(self):
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()

    def _monitor_loop(self):
        while True:
            try:
                self.check_directory()
            except Exception:
                pass
            time.sleep(2)

    def check_directory(self):
        if not self.ets_dir or not os.path.exists(self.ets_dir):
            self.root.after(0, lambda: self.status_label.config(text="无法访问 ETS 目录", foreground="red"))
            return

        try:
            items = os.listdir(self.ets_dir)
        except Exception:
            return

        exam_folders = [f for f in items if f.isdigit() and os.path.isdir(os.path.join(self.ets_dir, f))]
        
        updated = False
        new_sets = {}
        for folder in exam_folders:
            folder_path = os.path.join(self.ets_dir, folder)
            mtime = os.path.getmtime(folder_path)
            new_sets[folder] = mtime
            if folder not in self.known_sets or self.known_sets[folder] != mtime:
                updated = True

        if updated or len(new_sets) != len(self.known_sets):
            self.known_sets = new_sets
            self.root.after(0, self._update_tree_ui, exam_folders)

    def _update_tree_ui(self, exam_folders):
        sorted_folders = sorted(exam_folders, key=lambda f: os.path.getmtime(os.path.join(self.ets_dir, f)), reverse=True)

        self.tree.delete(*self.tree.get_children())
        latest_item_id = None
        for idx, folder in enumerate(sorted_folders):
            folder_path = os.path.join(self.ets_dir, folder)
            mtime_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(folder_path)))
            item_id = self.tree.insert("", tk.END, values=(folder, mtime_str))
            if idx == 0:
                latest_item_id = item_id

        count = len(sorted_folders)
        self.status_label.config(text=f"已载入 {count} 套试题", foreground="#198754" if not self.is_mini_mode else "#8BE9FD")

        # Automatically select & render the latest downloaded exam set
        if sorted_folders and latest_item_id:
            latest_id = sorted_folders[0]
            if self.current_folder_id != latest_id:
                self.tree.selection_set(latest_item_id)
                self.on_select_exam_set(None)

    def refresh_exam_list(self):
        self.check_directory()

    def on_select_exam_set(self, event):
        selected = self.tree.selection()
        if not selected:
            return

        folder_id = self.tree.item(selected[0])['values'][0]
        folder_path = os.path.join(self.ets_dir, str(folder_id))

        if not os.path.exists(folder_path):
            return

        self.current_folder_id = folder_id
        self.current_sections = ETSExtractor.parse_exam_set(folder_path)
        self.render_current_display()

    def render_current_display(self):
        if not self.current_folder_id or not self.current_sections:
            return

        self.text_display.config(state=tk.NORMAL)
        self.text_display.delete("1.0", tk.END)

        if self.is_mini_mode:
            # Dracula Slate Modern Theme for Mini Mode
            self.text_display.insert(tk.END, f"=== 试题库: {self.current_folder_id} ===\n\n", "mini_header")
            for sec in self.current_sections:
                stype = sec.section_type
                if stype == "empty":
                    continue

                self.text_display.insert(tk.END, f"大题 {sec.section_idx}: ", "mini_sec_title")

                if stype == "choice":
                    self.text_display.insert(tk.END, "【选择】 ", "tag_choice")
                    ans_str = "  ".join([f"{k}.{v['answer']}" for k, v in sorted(sec.choices.items(), key=lambda x: int(x[0]) if str(x[0]).isdigit() else str(x[0]))])
                    self.text_display.insert(tk.END, f"{ans_str}\n\n", "tag_ans_text")

                elif stype == "fill":
                    self.text_display.insert(tk.END, "【填空】\n", "tag_fill")
                    for k in sorted(sec.fills.keys(), key=lambda x: int(x) if str(x).isdigit() else str(x)):
                        ans = sec.fills[k]
                        self.text_display.insert(tk.END, f"  ({k}) ", "tag_label")
                        self.text_display.insert(tk.END, f"{ans}\n", "tag_ans_text")
                    self.text_display.insert(tk.END, "\n")

                elif stype == "dialogue":
                    self.text_display.insert(tk.END, "【问答】\n", "tag_qa")
                    for idx_q, item in enumerate(sec.dialogue, 1):
                        q_num = item['num'] if item['num'] else str(idx_q)
                        ans = item['std_answers'][0] if item['std_answers'] else item['keywords']
                        if ans:
                            self.text_display.insert(tk.END, f"  ({q_num}) ", "tag_label")
                            self.text_display.insert(tk.END, f"{ans}\n", "tag_ans_text")
                    self.text_display.insert(tk.END, "\n")

                elif stype == "retell":
                    self.text_display.insert(tk.END, "【复述】\n", "tag_retell")
                    if sec.dialogue and sec.dialogue[0].get('std_answers'):
                        full_ans = sec.dialogue[0]['std_answers'][0]
                        self.text_display.insert(tk.END, "  范文: ", "tag_label")
                        self.text_display.insert(tk.END, f"{full_ans}\n\n", "tag_ans_text")

                elif stype == "reading":
                    self.text_display.insert(tk.END, "【短文朗读】\n", "tag_read")
                    self.text_display.insert(tk.END, "  (全文朗读大题，无考题选项)\n\n", "tag_sub")

        else:
            # Full Detailed Mode
            folder_path = os.path.join(self.ets_dir, str(self.current_folder_id))
            self.text_display.insert(tk.END, f"试题库编号: {self.current_folder_id}\n", "h1")
            self.text_display.insert(tk.END, f"文件路径: {folder_path}\n", "sub")
            self.text_display.insert(tk.END, "="*70 + "\n\n")

            for sec in self.current_sections:
                idx = sec.section_idx
                folder_name = sec.folder_name
                has_content = False

                if sec.choices:
                    has_content = True
                    self.text_display.insert(tk.END, f"【部分 {idx}】 听后选择 ({folder_name})\n", "h2")
                    if sec.passage:
                        self.text_display.insert(tk.END, f"听力原文:\n{sec.passage}\n\n", "sub")

                    for num in sorted(sec.choices.keys(), key=lambda x: int(x) if str(x).isdigit() else str(x)):
                        item = sec.choices[num]
                        self.text_display.insert(tk.END, f"  Q{num}. {item['title']}\n")
                        for opt in item['options']:
                            self.text_display.insert(tk.END, f"     {opt}\n")
                        self.text_display.insert(tk.END, f"  [正确答案]: ", "sub")
                        self.text_display.insert(tk.END, f"{item['answer']}\n\n", "answer")

                if sec.fills:
                    has_content = True
                    self.text_display.insert(tk.END, f"【部分 {idx}】 听后记录 ({folder_name})\n", "h2")
                    if sec.passage:
                        self.text_display.insert(tk.END, f"材料原文:\n{sec.passage}\n\n", "sub")

                    self.text_display.insert(tk.END, "  [填空答案表]:\n")
                    for num in sorted(sec.fills.keys(), key=lambda x: int(x) if str(x).isdigit() else str(x)):
                        ans = sec.fills[num]
                        self.text_display.insert(tk.END, f"    ({num}) ", "sub")
                        self.text_display.insert(tk.END, f"{ans}\n", "answer")
                    self.text_display.insert(tk.END, "\n")

                if sec.dialogue:
                    has_content = True
                    self.text_display.insert(tk.END, f"【部分 {idx}】 听后回答 / 看图复述 ({folder_name})\n", "h2")
                    if sec.passage:
                        self.text_display.insert(tk.END, f"提示材料/短文:\n{sec.passage}\n\n", "sub")

                    for item in sec.dialogue:
                        ask_str = f"Q{item['num']}: {item['ask']}" if item['num'] else item['ask']
                        if ask_str:
                            self.text_display.insert(tk.END, f"  {ask_str}\n")
                        if item['std_answers']:
                            self.text_display.insert(tk.END, "  [标准参考答案]:\n", "sub")
                            for std in item['std_answers']:
                                self.text_display.insert(tk.END, f"    • {std}\n", "answer")
                        if item['keywords']:
                            self.text_display.insert(tk.END, f"  [机评核心词列表]: {item['keywords']}\n", "keyword")
                        self.text_display.insert(tk.END, "\n")

                if not sec.choices and not sec.fills and not sec.dialogue and sec.passage:
                    has_content = True
                    self.text_display.insert(tk.END, f"【部分 {idx}】 短文朗读 ({folder_name})\n", "h2")
                    self.text_display.insert(tk.END, f"{sec.passage}\n\n")

                if has_content:
                    self.text_display.insert(tk.END, "-"*50 + "\n\n")

        self.text_display.config(state=tk.DISABLED)

def main():
    root = tk.Tk()
    app = ETSHelperApp(root)
    root.mainloop()

if __name__ == '__main__':
    main()
