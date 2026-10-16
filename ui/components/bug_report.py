import tkinter as tk
import customtkinter as ctk
import threading
from tkinter import messagebox
from core.config import Config
from ui.components.dialog_utils import make_modal

class BugReportDialog(tk.Toplevel):
    def __init__(self, master, current_file=None):
        super().__init__(master)
        
        self.title("Сообщить об ошибке")
        self.geometry("500x400")
        self._close = make_modal(self, master)

        self.current_file = current_file
        self.cfg = Config()  # не self.config — см. settings_dialog.py

        root = ctk.CTkFrame(self, corner_radius=0)
        root.pack(fill="both", expand=True)
        root.grid_rowconfigure(1, weight=1)
        root.grid_columnconfigure(0, weight=1)
        
        ctk.CTkLabel(root, text="Опишите проблему (что делали, что ожидали, что произошло):",
                     font=("Segoe UI", 14, "bold")).grid(row=0, column=0, sticky="w", padx=10, pady=(10, 5))
        ctk.CTkLabel(root, text="К отчёту автоматически приложатся версия программы, имя файла и последние строки журнала.",
                     text_color="gray", font=("Segoe UI", 11)).grid(row=2, column=0, sticky="w", padx=10, pady=(0, 6))
        
        self.text_box = ctk.CTkTextbox(root, font=("Segoe UI", 12))
        self.text_box.grid(row=1, column=0, sticky="nsew", padx=10, pady=5)
        
        btn_frame = ctk.CTkFrame(root, fg_color="transparent")
        btn_frame.grid(row=3, column=0, sticky="e", padx=10, pady=10)
        
        self.cancel_btn = ctk.CTkButton(btn_frame, text="Отмена", width=100, fg_color="gray", hover_color="#555555", command=self._close)
        self.cancel_btn.pack(side="left", padx=(0, 10))
        
        self.send_btn = ctk.CTkButton(btn_frame, text="Отправить", width=120, command=self._send_report)
        self.send_btn.pack(side="left")

    def _send_report(self):
        desc = self.text_box.get("1.0", "end").strip()
        if not desc:
            messagebox.showwarning("Сообщить об ошибке", "Опишите проблему перед отправкой.", parent=self)
            return

        from core.bug_reporter import build_report, deliver
        report = build_report(desc, current_file=self.current_file)
        self.send_btn.configure(state="disabled", text="Отправка...")
        ui = getattr(self.master, "ui_call", self.after_idle)

        def _send():
            status, text = deliver(report)

            def done():
                parent = self.master
                if status == "saved":
                    messagebox.showwarning("Отчёт сохранён", text, parent=parent)
                else:
                    messagebox.showinfo("Отчёт отправлен", text, parent=parent)
                if self.winfo_exists():
                    self._close()
            ui(done)

        threading.Thread(target=_send, daemon=True).start()
