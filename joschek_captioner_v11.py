#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Joschek’s Captioner  v12  –  modern border-less UI, unlimited folders,
keyboard nav in editor, zoom pane, NO-HANG server shutdown,
NEW: "Filter & Move" tab for keyword-based file sorting.
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import subprocess
import os
import shutil
import threading
import signal
import glob
import base64
import json
from pathlib import Path
from openai import OpenAI
from PIL import Image, ImageTk

# ---------------- CONFIG ----------------
CONFIG_FILE = os.path.expanduser("~/.config/joschek_captioner.json")
DEFAULT_PORT = "11434"
DEFAULT_CTX = "8192"
DEFAULT_BATCH = "512"
DEFAULT_GPU = "99"
API_URL = f"http://localhost:{DEFAULT_PORT}/v1"
DEFAULT_PROMPT = "Describe this image in detail for an AI training dataset. Focus on clothing, background, textures, and lighting."

# ---------------- PALETTE ----------------
BG = "#2b2e37"
CARD = "#353945"
INPUT = "#3d424e"
TEXT = "#d3dae3"
DIM = "#7c818c"
BORDER = BG
BLUE = "#5294e2"
GREEN = "#73d216"
RED = "#cc0000"

# ---------------- UTILS ----------------
class Config:
    def __init__(self):
        self.config_dir = os.path.dirname(CONFIG_FILE)
        self.data = self.load()
    def load(self):
        try:
            os.makedirs(self.config_dir, exist_ok=True)
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE) as f:
                    return json.load(f)
        except Exception as e:
            print("Config load error:", e)
        return {
            "server_binary": "./build/bin/llama-server",
            "model_file": "",
            "projector_file": "",
            "port": DEFAULT_PORT,
            "context": DEFAULT_CTX,
            "gpu_layers": DEFAULT_GPU,
            "last_prompt": DEFAULT_PROMPT
        }
    def save(self):
        try:
            os.makedirs(self.config_dir, exist_ok=True)
            with open(CONFIG_FILE, "w") as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            print("Config save error:", e)
    def get(self, key, default=None):
        return self.data.get(key, default)
    def set(self, key, value):
        self.data[key] = value
        self.save()

# ---------------- WIDGETS ----------------
class QueueItem(tk.Frame):
    def __init__(self, parent, path, remove_cb, config):
        super().__init__(parent, bg=CARD)
        self.folder_path = path
        self.status = "pending"
        self.remove_cb = remove_cb
        self.config = config
        main = tk.Frame(self, bg=CARD)
        main.pack(fill="both", expand=True, padx=14, pady=10)
        header = tk.Frame(main, bg=CARD)
        header.pack(fill="x", pady=(0, 6))
        tk.Label(header, text=os.path.basename(path), bg=CARD, fg=TEXT,
                 font=("Sans", 9), anchor="w").pack(side="left", fill="x", expand=True)
        self.status_lbl = tk.Label(header, text="Ready", bg=CARD, fg=DIM, font=("Sans", 8))
        self.status_lbl.pack(side="left", padx=8)
        close = tk.Label(header, text="×", bg=CARD, fg=DIM, font=("Sans", 14), cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: remove_cb(self))
        close.bind("<Enter>", lambda e: close.config(fg=RED))
        close.bind("<Leave>", lambda e: close.config(fg=DIM))
        tk.Label(main, text=path, bg=CARD, fg=DIM, font=("Sans", 7), anchor="w").pack(fill="x", pady=(0, 8))
        self.prompt = tk.Text(main, height=2, bg=INPUT, fg=TEXT, bd=0, relief="flat",
                              font=("Sans", 8), insertbackground=BLUE, wrap="word")
        self.prompt.insert("1.0", config.get("last_prompt", DEFAULT_PROMPT))
        self.prompt.bind("<KeyRelease>", lambda e: config.set("last_prompt", self.get_prompt()))
        self.prompt.pack(fill="x")
    def set_status(self, state, msg=""):
        color = {"processing": BLUE, "done": GREEN, "error": RED}.get(state, DIM)
        self.status_lbl.config(text=msg, fg=color)
    def get_prompt(self):
        return self.prompt.get("1.0", "end-1c").strip()

class ScrollFrame(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=BG)
        canvas = tk.Canvas(self, bg=BG, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self.content = tk.Frame(canvas, bg=BG)
        self.content.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))

# ---------------- MAIN APP ----------------
class App:
    def __init__(self, root):
        self.root = root
        root.title("Joschek's Captioner v12")
        root.geometry("1100x720")
        root.configure(bg=BG)
        self.config = Config()
        self.setup_styles()
        self.server_proc = None
        self.batch_running = False
        self.queue = []
        self.client = None
        self.current_editor_folder = None
        self.editor_items = []
        self.thumb_size = 128
        # notebook
        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True)
        self.tab_srv = tk.Frame(nb, bg=BG)
        self.tab_batch = tk.Frame(nb, bg=BG)
        self.tab_editor = tk.Frame(nb, bg=BG)
        self.tab_filter = tk.Frame(nb, bg=BG)
        nb.add(self.tab_srv, text="Server")
        nb.add(self.tab_batch, text="Batch")
        nb.add(self.tab_editor, text="Editor")
        nb.add(self.tab_filter, text="Filter & Move")
        self.build_server()
        self.build_batch()
        self.build_editor()
        self.build_filter()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
    # ---------------- STYLES (ELEGANT) ----------------
    def setup_styles(self):
        s = ttk.Style()
        s.theme_use("clam")
        # uniform tab size
        s.configure("TNotebook", background=BG, borderwidth=0, tabmargins=[0, 0, 0, 0])
        s.configure("TNotebook.Tab", background=CARD, foreground=DIM,
                    padding=[24, 10], borderwidth=0, font=("Sans", 9))
        s.map("TNotebook.Tab", background=[("selected", INPUT)], foreground=[("selected", TEXT)])
        # flat progress
        s.configure("TProgressbar", background=BLUE, troughcolor=BG, borderwidth=0, thickness=4)
        # thin scrollbar
        s.configure("Vertical.TScrollbar", background=CARD, troughcolor=BG,
                    borderwidth=0, arrowsize=10, gripcount=0)
    # ---------------- SERVER TAB ----------------
    def build_server(self):
        f = tk.Frame(self.tab_srv, bg=BG)
        f.pack(fill="both", expand=True, padx=25, pady=20)
        self.bin = tk.StringVar(value=self.config.get("server_binary", "./build/bin/llama-server"))
        self.model = tk.StringVar(value=self.config.get("model_file", ""))
        self.proj = tk.StringVar(value=self.config.get("projector_file", ""))
        self.port = tk.StringVar(value=self.config.get("port", DEFAULT_PORT))
        self.ctx = tk.StringVar(value=self.config.get("context", DEFAULT_CTX))
        self.gpu = tk.StringVar(value=self.config.get("gpu_layers", DEFAULT_GPU))
        for var, key in [(self.bin, "server_binary"), (self.model, "model_file"),
                         (self.proj, "projector_file"), (self.port, "port"),
                         (self.ctx, "context"), (self.gpu, "gpu_layers")]:
            var.trace_add("write", lambda *_, v=var, k=key: self.config.set(k, v.get()))
        self.detect_binary()
        for label, var, browse in [("Server Binary", self.bin, True),
                                   ("Model (.gguf)", self.model, True),
                                   ("Projector (.gguf)", self.proj, True)]:
            self.field(f, label, var, browse)
        ttk.Frame(f, height=12).pack()
        params = tk.Frame(f, bg=BG)
        params.pack(fill="x")
        for lbl, v in [("Port", self.port), ("Context", self.ctx), ("GPU Layers", self.gpu)]:
            col = tk.Frame(params, bg=BG)
            col.pack(side="left", fill="x", expand=True, padx=3)
            tk.Label(col, text=lbl, bg=BG, fg=DIM, font=("Sans", 7)).pack(anchor="w", pady=(0, 2))
            tk.Entry(col, textvariable=v, bg=INPUT, fg=TEXT, bd=0, relief="flat",
                     font=("Sans", 8), insertbackground=BLUE, justify="center").pack(fill="x", ipady=5)
        ttk.Frame(f, height=8).pack()
        vram_frame = tk.Frame(f, bg=BG)
        vram_frame.pack(fill="x")
        self.vram_label = tk.Label(vram_frame, text="Checking VRAM...", bg=BG, fg=DIM, font=("Sans", 7))
        self.vram_label.pack(side="left", fill="x", expand=True)
        self.btn_kill_gpu = self.btn(vram_frame, "Kill GPU Processes", RED, self.kill_gpu_processes)
        self.btn_kill_gpu.pack(side="right")
        ttk.Frame(f, height=4).pack()
        tip = tk.Frame(f, bg=CARD)
        tip.pack(fill="x", padx=1, pady=1)
        tk.Label(tip, text="16GB VRAM defaults: Context 8192, GPU Layers 99, Batch 512",
                 bg=CARD, fg=DIM, font=("Sans", 7)).pack(pady=5)
        ttk.Frame(f, height=12).pack()
        btns = tk.Frame(f, bg=BG)
        btns.pack(fill="x")
        self.btn_start = self.btn(btns, "Start Server", GREEN, self.start_server)
        self.btn_start.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.btn_stop = self.btn(btns, "Stop Server", RED, self.stop_server)
        self.btn_stop.pack(side="left", fill="x", expand=True)
        self.btn_stop.config(state="disabled", bg=CARD)
        ttk.Frame(f, height=12).pack()
        log_frame = tk.Frame(f, bg=BG)
        log_frame.pack(fill="both", expand=True)
        self.log = scrolledtext.ScrolledText(log_frame, height=11, bg="#1a1d23", fg="#00ff00",
                                             bd=0, relief="flat", font=("Monospace", 7), wrap="word")
        self.log.pack(fill="both", expand=True)
    # ---------------- BATCH TAB ----------------
    def build_batch(self):
        main = tk.Frame(self.tab_batch, bg=BG)
        main.pack(fill="both", expand=True, padx=25, pady=15)
        left = tk.Frame(main, bg=BG)
        left.pack(side="left", fill="both", expand=True)
        tool = tk.Frame(left, bg=BG)
        tool.pack(fill="x", pady=(0, 10))
        self.btn(tool, "Add Folder", BLUE, self.add_folder).pack(side="left", padx=(0, 8))
        self.btn_proc = self.btn(tool, "Start Processing", GREEN, self.toggle_batch)
        self.btn_proc.pack(side="left")
        self.overwrite = tk.BooleanVar(value=False)
        tk.Checkbutton(tool, text="Overwrite", variable=self.overwrite, bg=BG, fg=TEXT,
                       selectcolor=INPUT, activebackground=BG, font=("Sans", 8),
                       highlightthickness=0).pack(side="right")
        self.queue_scroll = ScrollFrame(left)
        self.queue_scroll.pack(fill="both", expand=True)
        prog = tk.Frame(left, bg=BG)
        prog.pack(fill="x", side="bottom", pady=(10, 0))
        self.progress = ttk.Progressbar(prog, mode="determinate")
        self.progress.pack(fill="x")
        self.prog_lbl = tk.Label(prog, text="Idle", bg=BG, fg=DIM, font=("Sans", 8))
        self.prog_lbl.pack(pady=(4, 0))
        right = tk.Frame(main, bg=BG)
        right.pack(side="right", fill="both", expand=True, padx=(15, 0))
        tk.Label(right, text="Processing Status", bg=BG, fg=TEXT, font=("Sans", 9)).pack(anchor="w", pady=(0, 5))
        status_frame = tk.Frame(right, bg=BG)
        status_frame.pack(fill="both", expand=True)
        self.status_log = scrolledtext.ScrolledText(status_frame, bg=INPUT, fg=TEXT, bd=0, relief="flat",
                                                    font=("Monospace", 7), wrap="word", state="disabled")
        self.status_log.pack(fill="both", expand=True)
    # ---------------- EDITOR TAB ----------------
    def build_editor(self):
        tool = tk.Frame(self.tab_editor, bg=BG)
        tool.pack(fill="x", padx=25, pady=15)
        self.btn(tool, "Load Folder", BLUE, self.load_editor_folder).pack(side="left")
        self.editor_folder_label = tk.Label(tool, text="No folder loaded", bg=BG, fg=DIM, font=("Sans", 8))
        self.editor_folder_label.pack(side="left", padx=15)
        content = tk.Frame(self.tab_editor, bg=BG)
        content.pack(fill="both", expand=True, padx=25, pady=(0, 15))
        left = tk.Frame(content, bg=BG)
        left.pack(side="left", fill="both", expand=True)
        tk.Label(left, text="Images", bg=BG, fg=TEXT, font=("Sans", 8)).pack(anchor="w", pady=(0, 5))
        img_frame = tk.Frame(left, bg=BG)
        img_frame.pack(fill="both", expand=True)
        self.img_canvas = tk.Canvas(img_frame, bg=INPUT, highlightthickness=0, bd=0)
        img_scroll = ttk.Scrollbar(img_frame, orient="vertical", command=self.img_canvas.yview)
        self.img_list_frame = tk.Frame(self.img_canvas, bg=INPUT)
        self.img_list_frame.bind("<Configure>", lambda e: self.img_canvas.configure(scrollregion=self.img_canvas.bbox("all")))
        self.img_canvas.create_window((0, 0), window=self.img_list_frame, anchor="nw")
        self.img_canvas.configure(yscrollcommand=img_scroll.set)
        self.img_canvas.pack(side="left", fill="both", expand=True)
        img_scroll.pack(side="right", fill="y")
        right = tk.Frame(content, bg=BG)
        right.pack(side="right", fill="both", expand=True, padx=(15, 0))
        tk.Label(right, text="Caption", bg=BG, fg=TEXT, font=("Sans", 8)).pack(anchor="w", pady=(0, 5))
        text_frame = tk.Frame(right, bg=BG)
        text_frame.pack(fill="both", expand=True)
        self.editor_text = scrolledtext.ScrolledText(text_frame, bg=INPUT, fg=TEXT, bd=0, relief="flat",
                                                     font=("Sans", 9), wrap="word", insertbackground=BLUE)
        self.editor_text.pack(fill="both", expand=True)
        self.editor_text.bind("<KeyRelease>", self.autosave_caption)
        self.root.bind("<Up>", lambda e: self.editor_select_delta(-1))
        self.root.bind("<Down>", lambda e: self.editor_select_delta(1))
    # ---------------- FILTER & MOVE TAB ----------------
    def build_filter(self):
        f = tk.Frame(self.tab_filter, bg=BG)
        f.pack(fill="both", expand=True, padx=25, pady=20)
        # folder
        tk.Label(f, text="Image-Caption folder:", bg=BG, fg=DIM, font=("Sans", 9)).pack(anchor="w")
        self.filter_src_var = tk.StringVar()
        self.field(f, "", self.filter_src_var, False)
        # keyword
        ttk.Frame(f, height=8).pack()
        tk.Label(f, text="Keyword (case-insensitive):", bg=BG, fg=DIM, font=("Sans", 9)).pack(anchor="w")
        self.filter_kw_var = tk.StringVar()
        kw_entry = tk.Entry(f, textvariable=self.filter_kw_var, bg=INPUT, fg=TEXT, bd=0, relief="flat",
                            font=("Sans", 10), insertbackground=BLUE)
        kw_entry.pack(fill="x", ipady=6)
        # target
        ttk.Frame(f, height=8).pack()
        tk.Label(f, text="Target folder:", bg=BG, fg=DIM, font=("Sans", 9)).pack(anchor="w")
        self.filter_tgt_var = tk.StringVar()
        self.field(f, "", self.filter_tgt_var, False)
        # button
        ttk.Frame(f, height=15).pack()
        self.btn(f, "Move matched pairs", BLUE, self.move_keyword_pairs).pack(anchor="e")
        # log
        ttk.Frame(f, height=15).pack()
        log_frame = tk.Frame(f, bg=BG)
        log_frame.pack(fill="both", expand=True)
        self.filter_log = scrolledtext.ScrolledText(log_frame, height=10, bg=INPUT, fg=TEXT, bd=0, relief="flat",
                                                    font=("Monospace", 8), wrap="word", state="disabled")
        self.filter_log.pack(fill="both", expand=True)
    def move_keyword_pairs(self):
        src = self.filter_src_var.get()
        kw = self.filter_kw_var.get().strip().lower()
        tgt = self.filter_tgt_var.get()
        if not (src and kw and tgt):
            messagebox.showwarning("Input needed", "Please fill all fields.")
            return
        if not os.path.isdir(src) or not os.path.isdir(tgt):
            messagebox.showerror("Path error", "Source or target folder does not exist.")
            return
        matched = 0
        self.filter_log.config(state="normal")
        self.filter_log.delete("1.0", "end")
        self.filter_log.insert("end", f"Searching for keyword: {kw}\n")
        for ext in ["*.png", "*.jpg", "*.jpeg", "*.webp", "*.bmp"]:
            for img in glob.glob(os.path.join(src, ext)):
                txt = os.path.splitext(img)[0] + ".txt"
                if not os.path.isfile(txt):
                    continue
                try:
                    with open(txt, encoding="utf-8") as f:
                        content = f.read().lower()
                    if kw in content:
                        base = os.path.basename(img)
                        base_txt = os.path.basename(txt)
                        shutil.move(img, os.path.join(tgt, base))
                        shutil.move(txt, os.path.join(tgt, base_txt))
                        self.filter_log.insert("end", f"moved: {base}\n")
                        matched += 1
                except Exception as e:
                    self.filter_log.insert("end", f"error on {img}: {e}\n")
        self.filter_log.insert("end", f"Done. Moved {matched} pairs.\n")
        self.filter_log.config(state="disabled")
    # ---------------- EDITOR UTILS ----------------
    def editor_select_delta(self, delta):
        if not self.editor_items:
            return
        idx = next((i for i, (_, f) in enumerate(self.editor_items) if f["bg"] == INPUT), 0)
        new = max(0, min(len(self.editor_items) - 1, idx + delta))
        self.editor_items[new][1].event_generate("<Button-1>")
    def load_editor_folder(self):
        path = None
        if shutil.which("zenity"):
            try:
                path = subprocess.check_output(["zenity", "--file-selection", "--directory"],
                                              stderr=subprocess.DEVNULL).decode().strip()
            except:
                pass
        if not path:
            path = filedialog.askdirectory()
        if path:
            self.current_editor_folder = path
            self.editor_folder_label.config(text=os.path.basename(path))
            self.load_editor_images()
    def load_editor_images(self):
        for w in self.img_list_frame.winfo_children():
            w.destroy()
        self.editor_items = []
        self.editor_text.delete("1.0", "end")
        imgs = []
        for ext in ["*.png", "*.jpg", "*.jpeg", "*.webp", "*.bmp"]:
            imgs.extend(glob.glob(os.path.join(self.current_editor_folder, ext)))
            imgs.extend(glob.glob(os.path.join(self.current_editor_folder, ext.upper())))
        imgs.sort()
        for img_path in imgs:
            self.create_editor_item(img_path)
    def create_editor_item(self, img_path):
        item_frame = tk.Frame(self.img_list_frame, bg=CARD, cursor="hand2")
        item_frame.pack(fill="x", pady=2, padx=2)
        try:
            img = Image.open(img_path)
            img.thumbnail((self.thumb_size, self.thumb_size))
            photo = ImageTk.PhotoImage(img)
            img_label = tk.Label(item_frame, image=photo, bg=CARD)
            img_label.image = photo
            img_label.pack(side="left", padx=5, pady=5)
        except:
            img_label = tk.Label(item_frame, text="[img]", bg=CARD, fg=DIM, width=10)
            img_label.pack(side="left", padx=5, pady=5)
        name_label = tk.Label(item_frame, text=os.path.basename(img_path),
                              bg=CARD, fg=TEXT, font=("Sans", 8), anchor="w")
        name_label.pack(side="left", fill="x", expand=True, padx=5)
        def select():
            self.load_caption_for_image(img_path)
            for w in self.img_list_frame.winfo_children():
                w.config(bg=CARD)
                for child in w.winfo_children():
                    child.config(bg=CARD)
            item_frame.config(bg=INPUT)
            for child in item_frame.winfo_children():
                if isinstance(child, tk.Label):
                    child.config(bg=INPUT)
        item_frame.bind("<Button-1>", lambda e: select())
        for child in item_frame.winfo_children():
            child.bind("<Button-1>", lambda e: select())
        img_label.bind("<Double-Button-1>", lambda e: self.show_zoom(img_path))
        name_label.bind("<Double-Button-1>", lambda e: self.show_zoom(img_path))
        self.editor_items.append((img_path, item_frame))
    def show_zoom(self, img_path):
        if hasattr(self, "zoom_tl"):
            self.zoom_tl.destroy()
        tl = tk.Toplevel(self.root)
        tl.title(f"Zoom – {os.path.basename(img_path)}")
        tl.configure(bg=BG)
        tl.transient(self.root)
        x = self.root.winfo_x() - 550
        y = self.root.winfo_y() + 100
        tl.geometry(f"500x500+{x}+{y}")
        tl.focus()
        tl.bind("<Escape>", lambda e: tl.destroy())
        img = Image.open(img_path)
        img.thumbnail((480, 480), Image.LANCZOS)
        ph = ImageTk.PhotoImage(img)
        lbl = tk.Label(tl, image=ph, bg=BG)
        lbl.image = ph
        lbl.pack(expand=True)
        close = tk.Label(tl, text="✖  Close", fg=DIM, bg=BG, cursor="hand2")
        close.pack(pady=4)
        close.bind("<Button-1>", lambda e: tl.destroy())
        self.zoom_tl = tl
    def load_caption_for_image(self, img_path):
        txt_path = os.path.splitext(img_path)[0] + ".txt"
        self.current_caption_file = txt_path
        self.editor_text.delete("1.0", "end")
        if os.path.exists(txt_path):
            with open(txt_path, encoding="utf-8") as f:
                self.editor_text.insert("1.0", f.read())
    def autosave_caption(self, event=None):
        if hasattr(self, "current_caption_file"):
            try:
                content = self.editor_text.get("1.0", "end-1c")
                with open(self.current_caption_file, "w", encoding="utf-8") as f:
                    f.write(content)
            except Exception as e:
                print("Autosave error:", e)
    # ---------------- GENERIC UI ----------------
    def field(self, parent, label, var, browse):
        f = tk.Frame(parent, bg=BG)
        f.pack(fill="x", pady=3)
        if label:
            tk.Label(f, text=label, bg=BG, fg=DIM, font=("Sans", 8), width=14, anchor="w").pack(side="left")
        entry_frame = tk.Frame(f, bg=BG)
        entry_frame.pack(side="left", fill="x", expand=True)
        tk.Entry(entry_frame, textvariable=var, bg=INPUT, fg=TEXT, bd=0, relief="flat",
                 font=("Sans", 8), insertbackground=BLUE).pack(fill="both", expand=True, ipady=4)
        tk.Button(f, text="…", bg=CARD, fg=TEXT, bd=0, relief="flat", cursor="hand2",
                  activebackground=INPUT,
                  command=lambda: self.browse(var, browse)).pack(side="left", padx=(4, 0), ipady=3, ipadx=8)
    def btn(self, parent, text, color, cmd):
        return tk.Button(parent, text=text, bg=color, fg="white", bd=0, relief="flat",
                         activebackground=color, activeforeground="white",
                         font=("Sans", 8, "bold"), command=cmd, cursor="hand2", padx=16, pady=6)
    # ---------------- SERVER  ----------------
    def detect_binary(self):
        for p in ["./build/bin/llama-server", "./llama-server", "../llama.cpp/build/bin/llama-server"]:
            if os.path.exists(p):
                self.bin.set(p)
                break
    def update_vram_info(self):
        try:
            used, total = map(int, subprocess.check_output(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total",
                 "--format=csv,noheader,nounits"], stderr=subprocess.DEVNULL).decode().strip().split(","))
            free, percent = total - used, (used / total) * 100
            color = GREEN if percent < 50 else (BLUE if percent < 80 else RED)
            self.vram_label.config(text=f"VRAM: {used}MB used / {free}MB free / {total}MB total ({percent:.0f}%)", fg=color)
        except:
            self.vram_label.config(text="VRAM info unavailable", fg=DIM)
    def kill_gpu_processes(self):
        if not messagebox.askyesno("Kill GPU Processes", "Terminate ALL GPU processes?"):
            return
        try:
            pids = map(int, subprocess.check_output(
                ["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader"],
                stderr=subprocess.DEVNULL).decode().strip().split())
            killed = 0
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                    killed += 1
                except:
                    pass
            threading.Timer(1.0, self.update_vram_info).start()
            messagebox.showinfo("GPU Processes", f"Killed {killed} process(es).")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to kill GPU processes:\n{e}")
    def browse(self, var, is_file):
        if shutil.which("zenity"):
            cmd = ["zenity", "--file-selection"]
            if not is_file:
                cmd.append("--directory")
            try:
                r = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip()
                if r:
                    var.set(r)
                    return
            except:
                pass
        path = filedialog.askopenfilename() if is_file else filedialog.askdirectory()
        if path:
            var.set(path)
    # ---------------- SERVER CONTROL (NO-HANG) ----------------
    def start_server(self):
        cmd = [self.bin.get(), "-m", self.model.get(), "--port", self.port.get(),
               "--ctx-size", self.ctx.get(), "-ngl", self.gpu.get(), "-b", DEFAULT_BATCH]
        if self.proj.get():
            cmd.extend(["--mmproj", self.proj.get()])
        self.log.insert("end", "Starting server...\n")
        try:
            self.server_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                               stderr=subprocess.STDOUT, text=True,
                                               bufsize=1, preexec_fn=os.setsid)
            self.btn_start.config(state="disabled", bg=CARD)
            self.btn_stop.config(state="normal", bg=RED)
            threading.Thread(target=self.watch_server, daemon=True).start()
        except Exception as e:
            self.log.insert("end", f"Error: {e}\n")
    def stop_server(self):
        if self.server_proc:
            try:
                os.killpg(os.getpgid(self.server_proc.pid), signal.SIGTERM)
                threading.Thread(target=self.server_proc.wait, daemon=True).start()
            except Exception as e:
                self.log.insert("end", f"Stop error: {e}\n")
        self.root.after(100, self.reset_ui)
    def watch_server(self):
        try:
            for line in iter(self.server_proc.stdout.readline, ""):
                if line:
                    self.log.insert("end", line)
                    self.log.see("end")
        except Exception:
            pass
        self.root.after(0, self.reset_ui)
    def reset_ui(self):
        self.btn_start.config(state="normal", bg=GREEN)
        self.btn_stop.config(state="disabled", bg=CARD)
        self.log.insert("end", "Server stopped\n")
    # ---------------- BATCH ----------------
    def add_folder(self):
        path = None
        if shutil.which("zenity"):
            try:
                path = subprocess.check_output(["zenity", "--file-selection", "--directory"],
                                              stderr=subprocess.DEVNULL).decode().strip()
            except:
                pass
        if not path:
            path = filedialog.askdirectory()
        if path:
            item = QueueItem(self.queue_scroll.content, path, self.remove_item, self.config)
            item.pack(fill="x", pady=(0, 6))
            self.queue.append(item)
    def remove_item(self, item):
        if item.status != "processing":
            item.destroy()
            if item in self.queue:
                self.queue.remove(item)
    def log_status(self, msg):
        self.status_log.config(state="normal")
        self.status_log.insert("end", f"{msg}\n")
        self.status_log.see("end")
        self.status_log.config(state="disabled")
    def toggle_batch(self):
        if self.batch_running:
            self.batch_running = False
            self.btn_proc.config(text="Start Processing", bg=GREEN)
            self.prog_lbl.config(text="Stopping...")
        else:
            try:
                self.client = OpenAI(base_url=API_URL, api_key="sk-no-key")
                self.client.models.list()
            except Exception as e:
                messagebox.showerror("Connection Error", f"Cannot connect to server.\n{e}")
                return
            self.status_log.config(state="normal")
            self.status_log.delete("1.0", "end")
            self.status_log.config(state="disabled")
            self.batch_running = True
            self.btn_proc.config(text="Stop Processing", bg=RED)
            threading.Thread(target=self.run_batch, daemon=True).start()
    def run_batch(self):
        total = len(self.queue)
        if total == 0:
            self.batch_running = False
            self.root.after(0, lambda: self.btn_proc.config(text="Start Processing", bg=GREEN))
            self.root.after(0, lambda: self.prog_lbl.config(text="No folders in queue"))
            return
        for idx, item in enumerate(self.queue):
            if not self.batch_running:
                break
            self.root.after(0, lambda i=item: i.set_status("processing", "Scanning..."))
            imgs = []
            for ext in ["*.png", "*.jpg", "*.jpeg", "*.webp", "*.bmp"]:
                imgs.extend(glob.glob(os.path.join(item.folder_path, ext)))
                imgs.extend(glob.glob(os.path.join(item.folder_path, ext.upper())))
            total_imgs = len(imgs)
            done = 0
            for img in imgs:
                if not self.batch_running:
                    break
                txt = os.path.splitext(img)[0] + ".txt"
                if os.path.exists(txt) and not self.overwrite.get():
                    done += 1
                    continue
                try:
                    prompt = item.get_prompt() or DEFAULT_PROMPT
                    with open(img, "rb") as f:
                        img_b64 = base64.b64encode(f.read()).decode()
                    resp = self.client.chat.completions.create(
                        model=os.path.basename(self.model.get()),
                        messages=[{"role": "user", "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
                        ]}],
                        max_tokens=300
                    )
                    with open(txt, "w", encoding="utf-8") as f:
                        f.write(resp.choices[0].message.content.strip())
                    done += 1
                    img_name = os.path.basename(img)
                    self.root.after(0, lambda name=img_name: self.log_status(f"✓ {name}"))
                except Exception as e:
                    img_name = os.path.basename(img)
                    self.root.after(0, lambda name=img_name, err=str(e): self.log_status(f"✗ {name}: {err}"))
                pct = int(((idx + (done / total_imgs)) / total) * 100) if total_imgs > 0 else 0
                self.root.after(0, lambda p=pct: self.progress.configure(value=p))
                self.root.after(0, lambda d=done, t=total_imgs, i=item:
                               i.set_status("processing", f"{d}/{t}"))
            self.root.after(0, lambda i=item, r=self.batch_running:
                           i.set_status("done" if r else "error", "Complete" if r else "Stopped"))
        self.batch_running = False
        self.root.after(0, lambda: self.btn_proc.config(text="Start Processing", bg=GREEN))
        self.root.after(0, lambda: self.prog_lbl.config(text="Idle"))
    # ---------------- CLEAN EXIT ----------------
    def on_close(self):
        if self.server_proc:
            self.stop_server()
        self.root.destroy()

# ---------------- RUN ----------------
if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()